package dev.bubuki.proactive;

import android.content.ContentValues;
import android.content.Context;
import android.graphics.Bitmap;
import android.graphics.BitmapFactory;
import android.net.Uri;
import android.os.Environment;
import android.provider.MediaStore;
import android.util.Base64;
import android.webkit.MimeTypeMap;
import org.json.JSONObject;

/** Chunked encrypted transfer into Android Downloads, including automatic image compression & failure cleanup. */
final class CloudFiles {
    private static boolean tryCompressImage(Context context, Uri uri, String name, java.io.File targetFile) {
        String lower = name == null ? "" : name.toLowerCase(java.util.Locale.ROOT);
        if (!(lower.endsWith(".jpg") || lower.endsWith(".jpeg") || lower.endsWith(".png") || lower.endsWith(".webp"))) {
            return false;
        }
        try {
            BitmapFactory.Options bounds = new BitmapFactory.Options();
            bounds.inJustDecodeBounds = true;
            try (java.io.InputStream in = context.getContentResolver().openInputStream(uri)) {
                if (in == null) return false;
                BitmapFactory.decodeStream(in, null, bounds);
            }
            int w = bounds.outWidth, h = bounds.outHeight;
            if (w <= 0 || h <= 0) return false;
            int maxSide = Math.max(w, h);
            int sample = 1;
            while (maxSide / (sample * 2) >= 1600) {
                sample *= 2;
            }
            BitmapFactory.Options opts = new BitmapFactory.Options();
            opts.inSampleSize = sample;
            Bitmap decoded;
            try (java.io.InputStream in = context.getContentResolver().openInputStream(uri)) {
                if (in == null) return false;
                decoded = BitmapFactory.decodeStream(in, null, opts);
            }
            if (decoded == null) return false;
            int dw = decoded.getWidth(), dh = decoded.getHeight();
            int longest = Math.max(dw, dh);
            if (longest > 1600) {
                float ratio = 1600f / longest;
                int nw = Math.max(1, Math.round(dw * ratio));
                int nh = Math.max(1, Math.round(dh * ratio));
                Bitmap scaled = Bitmap.createScaledBitmap(decoded, nw, nh, true);
                if (scaled != decoded) decoded.recycle();
                decoded = scaled;
            }
            try (java.io.OutputStream out = new java.io.FileOutputStream(targetFile)) {
                Bitmap.CompressFormat fmt = lower.endsWith(".png") && longest <= 1080
                    ? Bitmap.CompressFormat.PNG
                    : Bitmap.CompressFormat.JPEG;
                decoded.compress(fmt, 82, out);
            } finally {
                decoded.recycle();
            }
            return targetFile.exists() && targetFile.length() > 0;
        } catch (Throwable ignored) {
            return false;
        }
    }

    static JSONObject upload(Context context,Uri uri,String source) throws Exception {
        if(!"workspace".equals(source)&&!"computer".equals(source))throw new Exception("文件来源无效");
        String name="attachment";
        long rawSize=-1;
        try(android.database.Cursor cursor=context.getContentResolver().query(uri,new String[]{android.provider.OpenableColumns.DISPLAY_NAME,android.provider.OpenableColumns.SIZE},null,null,null)){
            if(cursor!=null&&cursor.moveToFirst()){
                name=cursor.getString(0);
                if(!cursor.isNull(1))rawSize=cursor.getLong(1);
            }
        }
        if(name==null)name="attachment";name=name.replaceAll("[\\\\/:*?\"<>|\\p{Cntrl}]","_");
        if(name.startsWith("."))name="file"+name;if(name.length()>150)name=name.substring(name.length()-150);
        java.io.File staged=java.io.File.createTempFile("pulse-upload-",".partial",context.getCacheDir());
        SecureTransport transport=new SecureTransport(context);String endpoint="computer".equals(source)?"/api/computer/uploads":"/api/files/uploads",uploadId=null;
        try {
            java.security.MessageDigest digest=java.security.MessageDigest.getInstance("SHA-256");long total=0;
            boolean compressed = (rawSize < 0 || rawSize > 450_000) && tryCompressImage(context, uri, name, staged);
            if (compressed) {
                try (java.io.InputStream input = new java.io.FileInputStream(staged)) {
                    byte[] buffer = new byte[262144];
                    int n;
                    while ((n = input.read(buffer)) != -1) {
                        total += n;
                        digest.update(buffer, 0, n);
                    }
                }
            } else {
                try(java.io.InputStream input=context.getContentResolver().openInputStream(uri);java.io.OutputStream output=new java.io.FileOutputStream(staged)){
                    if(input==null)throw new Exception("无法读取所选文件");byte[] buffer=new byte[262144];int n;
                    while((n=input.read(buffer))!=-1){total+=n;if(total>50_000_000)throw new Exception("文件最大 50 MB");output.write(buffer,0,n);digest.update(buffer,0,n);}
                }
            }
            StringBuilder hash=new StringBuilder();for(byte b:digest.digest())hash.append(String.format(java.util.Locale.ROOT,"%02x",b&255));
            JSONObject started=transport.request("POST",endpoint,new JSONObject().put("name",name).put("size",total).put("sha256",hash.toString()),null);uploadId=started.getString("id");
            long offset=0;
            try(java.io.InputStream input=new java.io.FileInputStream(staged)){
                byte[] buffer=new byte[262144];int n;
                while((n=input.read(buffer))!=-1){JSONObject packet=transport.request("PUT",endpoint+"/"+uploadId,new JSONObject().put("offset",offset).put("data",Base64.encodeToString(buffer,0,n,Base64.NO_WRAP)),null);offset+=n;if(packet.getLong("offset")!=offset)throw new Exception("上传分块不连续");}
            }
            JSONObject receipt=transport.request("POST",endpoint+"/"+uploadId+"/finish",new JSONObject(),null);
            if(receipt.getLong("bytes")!=total||!receipt.getString("sha256").equals(hash.toString()))throw new Exception("上传回读摘要不一致");
            uploadId=null;return receipt.put("name",name);
        } finally {
            if(uploadId!=null)try{transport.request("DELETE",endpoint+"/"+uploadId,null,null);}catch(Exception ignored){}
            staged.delete();
        }
    }
    static JSONObject download(Context context,String source,String path) throws Exception {
        if(!"computer".equals(source)&&!"workspace".equals(source))throw new Exception("文件来源无效");
        String endpoint="computer".equals(source)?"/api/computer/file":"/api/files/file";
        String name=path.substring(path.lastIndexOf('/')+1).replaceAll("[\\\\/:*?\"<>|\\p{Cntrl}]","_");
        if(name.isEmpty()||name.length()>180)throw new Exception("文件名无效");
        String extension=MimeTypeMap.getFileExtensionFromUrl(name),mime=MimeTypeMap.getSingleton().getMimeTypeFromExtension(extension.toLowerCase(java.util.Locale.ROOT));
        ContentValues values=new ContentValues();values.put(MediaStore.Downloads.DISPLAY_NAME,name);
        values.put(MediaStore.Downloads.MIME_TYPE,mime==null?"application/octet-stream":mime);
        values.put(MediaStore.Downloads.RELATIVE_PATH,Environment.DIRECTORY_DOWNLOADS+"/Pulse Bot");values.put(MediaStore.Downloads.IS_PENDING,1);
        Uri target=context.getContentResolver().insert(MediaStore.Downloads.EXTERNAL_CONTENT_URI,values);
        if(target==null)throw new Exception("下载目录不可用");
        long offset=0,expected=-1;boolean complete=false;String version=null;
        try {
            SecureTransport transport=new SecureTransport(context);
            try(java.io.OutputStream output=context.getContentResolver().openOutputStream(target,"w")){
                if(output==null)throw new Exception("无法写入下载文件");
                while(true){
                    JSONObject query=new JSONObject().put("path",path).put("offset",offset).put("size",262144);if(version!=null)query.put("version",version);
                    JSONObject packet=transport.request("GET",endpoint,null,query);if(packet.has("version"))version=packet.getString("version");
                    long total=packet.getLong("size");if(total<0||total>50_000_000||packet.getLong("offset")!=offset||expected!=-1&&expected!=total)throw new Exception("文件传输参数不一致");
                    expected=total;byte[] data=Base64.decode(packet.getString("data"),Base64.DEFAULT);
                    if(data.length>262144||packet.getLong("next_offset")!=offset+data.length||offset+data.length>expected)throw new Exception("文件分块不连续");
                    output.write(data);offset+=data.length;
                    if(packet.getBoolean("eof")){if(offset!=expected)throw new Exception("文件下载不完整");break;}
                    if(data.length==0)throw new Exception("文件传输中断");
                }
            }
            values.clear();values.put(MediaStore.Downloads.IS_PENDING,0);context.getContentResolver().update(target,values,null,null);complete=true;
            return new JSONObject().put("name",name).put("bytes",offset).put("uri",target.toString());
        } finally {if(!complete)context.getContentResolver().delete(target,null,null);}
    }
}
