package dev.bubuki.proactive;
import android.content.Context;
import org.json.*;
final class VirtualDisplayEngine {
    private final Context context;private final PrivilegeManager privilege;
    private JSONObject state=new JSONObject();
    VirtualDisplayEngine(Context context,PrivilegeManager privilege){this.context=context;this.privilege=privilege;}
    synchronized JSONObject status(){return state;}
    synchronized JSONObject create(int w,int h)throws Exception{state=privilege.execute(new JSONObject().put("op","create").put("width",w).put("height",h));return state;}
    synchronized void release()throws Exception{if(state.optBoolean("created"))state=privilege.execute(new JSONObject().put("op","release"));}
    synchronized JSONObject capture()throws Exception{return privilege.execute(new JSONObject().put("op","capture"));}
    synchronized JSONObject launch(String component)throws Exception{return privilege.execute(new JSONObject().put("op","launch").put("component",component));}
    synchronized JSONObject tap(int x,int y)throws Exception{return privilege.execute(new JSONObject().put("op","tap").put("x",x).put("y",y));}
    synchronized JSONObject visionStep(String goal,boolean execute)throws Exception{
        JSONObject shot=capture();
        JSONObject decision=new SecureTransport(context).request("POST","/api/vision",new JSONObject().put("image",shot.getString("image")).put("goal",goal).put("width",shot.getInt("width")).put("height",shot.getInt("height")).put("display_id",shot.getInt("displayId")),null);
        if(execute&&decision.optString("action").equals("tap")){
            if(decision.getInt("display_id")!=shot.getInt("displayId"))throw new Exception("VLM displayId 不匹配");
            decision.put("execution",tap(decision.getInt("x"),decision.getInt("y")));
        }
        return decision;
    }
    void close(){try{release();}catch(Exception ignored){}}
}
