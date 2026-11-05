package dev.bubuki.proactive;

import android.content.Context;
import android.security.keystore.KeyGenParameterSpec;
import android.security.keystore.KeyProperties;
import android.util.Base64;
import org.json.JSONObject;
import java.nio.charset.StandardCharsets;
import java.security.KeyStore;
import javax.crypto.Cipher;
import javax.crypto.KeyGenerator;
import javax.crypto.SecretKey;
import javax.crypto.spec.GCMParameterSpec;

final class SecurePrefs {
    private static final String ALIAS="pulse.settings.v1";
    private static SecretKey key() throws Exception {
        KeyStore store=KeyStore.getInstance("AndroidKeyStore");store.load(null);
        if(!store.containsAlias(ALIAS)) {
            KeyGenerator gen=KeyGenerator.getInstance(KeyProperties.KEY_ALGORITHM_AES,"AndroidKeyStore");
            gen.init(new KeyGenParameterSpec.Builder(ALIAS,KeyProperties.PURPOSE_ENCRYPT|KeyProperties.PURPOSE_DECRYPT)
                .setBlockModes(KeyProperties.BLOCK_MODE_GCM).setEncryptionPaddings(KeyProperties.ENCRYPTION_PADDING_NONE).build());
            gen.generateKey();
        }
        return (SecretKey)store.getKey(ALIAS,null);
    }
    static synchronized JSONObject read(Context context) throws Exception {
        String raw=context.getSharedPreferences("pulse",0).getString("encrypted","");
        JSONObject result;
        if(raw.isEmpty()){
            result=new JSONObject().put("serverUrl",SecureTransport.DEFAULT_SERVER)
                .put("deviceToken",SecureTransport.defaultToken())
                .put("background",true);
        } else {
            JSONObject data=new JSONObject(raw);
            Cipher cipher=Cipher.getInstance("AES/GCM/NoPadding");
            cipher.init(Cipher.DECRYPT_MODE,key(),new GCMParameterSpec(128,Base64.decode(data.getString("iv"),Base64.NO_WRAP)));
            result=new JSONObject(new String(cipher.doFinal(Base64.decode(data.getString("data"),Base64.NO_WRAP)),StandardCharsets.UTF_8));
            if(result.optString("serverUrl","").trim().isEmpty()) result.put("serverUrl",SecureTransport.DEFAULT_SERVER);
            if(result.optString("deviceToken","").trim().isEmpty()) result.put("deviceToken",SecureTransport.defaultToken());
        }
        return result;
    }
    static synchronized void save(Context context,JSONObject settings) throws Exception {
        Cipher cipher=Cipher.getInstance("AES/GCM/NoPadding");cipher.init(Cipher.ENCRYPT_MODE,key());
        JSONObject packet=new JSONObject().put("iv",Base64.encodeToString(cipher.getIV(),Base64.NO_WRAP))
            .put("data",Base64.encodeToString(cipher.doFinal(settings.toString().getBytes(StandardCharsets.UTF_8)),Base64.NO_WRAP));
        if(!context.getSharedPreferences("pulse",0).edit().putString("encrypted",packet.toString()).commit())throw new Exception("本地加密保存失败");
    }
}
