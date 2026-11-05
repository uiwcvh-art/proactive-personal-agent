package dev.bubuki.proactive;

import android.content.*;
import android.graphics.*;
import android.net.Uri;
import android.provider.MediaStore;
import android.util.Base64;
import androidx.core.content.FileProvider;
import org.json.JSONObject;
import java.io.*;
import java.nio.ByteBuffer;

/** User-selected photos are oriented and resized locally before their square crop is synced. */
final class AvatarImages {
    static final int PHOTO_LIMIT=25*1024*1024;
    static File cameraFile(Context context){return new File(context.getCacheDir(),"avatar-capture/photo.jpg");}
    static Intent cameraIntent(Context context)throws Exception {
        File file=cameraFile(context);file.getParentFile().mkdirs();
        Uri uri=FileProvider.getUriForFile(context,context.getPackageName()+".avatar-files",file);
        Intent intent=new Intent(MediaStore.ACTION_IMAGE_CAPTURE).putExtra(MediaStore.EXTRA_OUTPUT,uri);
        intent.setClipData(ClipData.newRawUri("Avatar",uri));
        intent.addFlags(Intent.FLAG_GRANT_WRITE_URI_PERMISSION|Intent.FLAG_GRANT_READ_URI_PERMISSION);
        return intent;
    }
    static JSONObject read(Context context,Uri uri)throws Exception {
        byte[] bytes;
        try(InputStream in=context.getContentResolver().openInputStream(uri)){bytes=Io.read(in,PHOTO_LIMIT+1);}
        if(bytes.length>PHOTO_LIMIT)throw new IOException("图片不能超过 25 MB");
        Bitmap bitmap=ImageDecoder.decodeBitmap(ImageDecoder.createSource(ByteBuffer.wrap(bytes)),(decoder,info,source)->{
            int width=info.getSize().getWidth(),height=info.getSize().getHeight();
            double ratio=Math.min(1.0,1536.0/Math.max(width,height));
            decoder.setTargetSize(Math.max(1,(int)(width*ratio)),Math.max(1,(int)(height*ratio)));
            decoder.setAllocator(ImageDecoder.ALLOCATOR_SOFTWARE);
            decoder.setTargetColorSpace(ColorSpace.get(ColorSpace.Named.SRGB));
        });
        try {
            ByteArrayOutputStream out=new ByteArrayOutputStream();
            bitmap.compress(Bitmap.CompressFormat.PNG,100,out);
            return new JSONObject().put("image","data:image/png;base64,"+Base64.encodeToString(out.toByteArray(),Base64.NO_WRAP))
                .put("width",bitmap.getWidth()).put("height",bitmap.getHeight());
        } finally {bitmap.recycle();}
    }
}
