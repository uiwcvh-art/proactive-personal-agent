package dev.bubuki.proactive;

import android.content.Context;
import android.util.Base64;
import org.json.JSONObject;
import java.nio.charset.StandardCharsets;
import java.security.KeyFactory;
import java.security.SecureRandom;
import java.security.spec.MGF1ParameterSpec;
import java.security.spec.X509EncodedKeySpec;
import java.util.concurrent.TimeUnit;
import javax.crypto.Cipher;
import javax.crypto.spec.GCMParameterSpec;
import javax.crypto.spec.OAEPParameterSpec;
import javax.crypto.spec.PSource;
import javax.crypto.spec.SecretKeySpec;
import okhttp3.*;

final class SecureTransport {
    static final String DEFAULT_SERVER="http://216.23.82.196/proactive-agent";
    private static final String TOKEN_A="";
    private static final String TOKEN_B="";
    static String defaultToken(){return TOKEN_A+TOKEN_B;}
    static final OkHttpClient HTTP=new OkHttpClient.Builder().connectTimeout(30,TimeUnit.SECONDS)
        .readTimeout(300,TimeUnit.SECONDS).writeTimeout(60,TimeUnit.SECONDS).retryOnConnectionFailure(true).build();
    static final MediaType JSON=MediaType.get("application/json; charset=utf-8");
    private final Context context;
    SecureTransport(Context context){this.context=context.getApplicationContext();}
    static class Session {byte[] key;JSONObject packet;String base;}
    Session session(JSONObject inner) throws Exception {
        JSONObject settings=SecurePrefs.read(context);
        String base=settings.optString("serverUrl",DEFAULT_SERVER).replaceAll("/+$","");
        HttpUrl url=HttpUrl.parse(base);
        if(url==null||(!url.scheme().equals("http")&&!url.scheme().equals("https"))||!url.username().isEmpty()||!url.password().isEmpty())throw new Exception("Server URL 无效");
        String token=settings.optString("deviceToken","").trim();
        if(token.isEmpty())token=defaultToken();
        String publicKey=settings.optString("publicKey","").trim();
        if(publicKey.isEmpty()){
            try(var in=context.getAssets().open("transport.json")){
                publicKey=new JSONObject(new String(Io.read(in,20000),StandardCharsets.UTF_8)).getString("public_key");
            }
        }
        Session result=new Session();result.key=new byte[32];new SecureRandom().nextBytes(result.key);result.base=base;
        inner.put("token",token).put("timestamp",System.currentTimeMillis()/1000.0);
        result.packet=encrypt(result.key,inner);
        Cipher rsa=Cipher.getInstance("RSA/ECB/OAEPWithSHA-256AndMGF1Padding");
        rsa.init(Cipher.ENCRYPT_MODE,KeyFactory.getInstance("RSA").generatePublic(new X509EncodedKeySpec(Base64.decode(publicKey,Base64.DEFAULT))),
            new OAEPParameterSpec("SHA-256","MGF1",MGF1ParameterSpec.SHA256,PSource.PSpecified.DEFAULT));
        result.packet.put("key",Base64.encodeToString(rsa.doFinal(result.key),Base64.NO_WRAP));
        return result;
    }
    static JSONObject encrypt(byte[] key,JSONObject data) throws Exception {
        byte[] iv=new byte[12];new SecureRandom().nextBytes(iv);
        Cipher cipher=Cipher.getInstance("AES/GCM/NoPadding");
        cipher.init(Cipher.ENCRYPT_MODE,new SecretKeySpec(key,"AES"),new GCMParameterSpec(128,iv));
        return new JSONObject().put("nonce",Base64.encodeToString(iv,Base64.NO_WRAP))
            .put("data",Base64.encodeToString(cipher.doFinal(data.toString().getBytes(StandardCharsets.UTF_8)),Base64.NO_WRAP));
    }
    static JSONObject decrypt(byte[] key,JSONObject packet) throws Exception {
        Cipher cipher=Cipher.getInstance("AES/GCM/NoPadding");
        cipher.init(Cipher.DECRYPT_MODE,new SecretKeySpec(key,"AES"),new GCMParameterSpec(128,Base64.decode(packet.getString("nonce"),Base64.NO_WRAP)));
        return new JSONObject(new String(cipher.doFinal(Base64.decode(packet.getString("data"),Base64.NO_WRAP)),StandardCharsets.UTF_8));
    }
    JSONObject request(String method,String path,JSONObject body,JSONObject query) throws Exception {
        if(!path.startsWith("/api/")||path.equals("/api/secure"))throw new Exception("不支持的 API 路径");
        JSONObject inner=new JSONObject().put("method",method).put("path",path);
        if(body!=null)inner.put("body",body);if(query!=null)inner.put("query",query);
        Session s=session(inner);
        Request req=new Request.Builder().url(s.base+"/api/secure").post(RequestBody.create(s.packet.toString(),JSON)).build();
        try(Response response=HTTP.newCall(req).execute()){
            if(response.body()==null)throw new Exception("服务器没有响应内容");
            if(!response.isSuccessful())throw new Exception("连接失败 HTTP "+response.code()+"，检查服务器地址与设备时间");
            JSONObject result=decrypt(s.key,new JSONObject(response.body().string()));
            if(result.getInt("status")>=400)throw new Exception("HTTP "+result.getInt("status")+" · "+result.getJSONObject("body").optString("detail","请求未完成"));
            return result.getJSONObject("body");
        }
    }
}
