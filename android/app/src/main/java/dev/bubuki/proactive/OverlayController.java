package dev.bubuki.proactive;
import android.content.*;
import android.graphics.Color;
import android.graphics.Typeface;
import android.graphics.drawable.GradientDrawable;
import android.os.Build;
import android.provider.Settings;
import android.view.*;
import android.widget.*;
import org.json.*;

final class OverlayController {
    private final Context context;private final WindowManager manager;private View view;private android.animation.ValueAnimator dimAnimator;private String current="";private boolean replying=false;
    OverlayController(Context c){context=c;manager=c.getSystemService(WindowManager.class);}
    private int dp(int v){return (int)(v*context.getResources().getDisplayMetrics().density);}
    private TextView text(String t,int size,int color){TextView v=new TextView(context);v.setText(t);v.setTextSize(size);v.setTextColor(color);v.setPadding(0,dp(7),0,dp(7));return v;}
    private boolean animate(){try{return android.animation.ValueAnimator.areAnimatorsEnabled()&&SecurePrefs.read(context).optBoolean("motion",true);}catch(Exception ignored){return false;}}
    void show(String id,String message){
        if(!Settings.canDrawOverlays(context)||id.equals(current)&&view!=null)return;
        hide();current=id;
        LinearLayout card=new LinearLayout(context);card.setOrientation(LinearLayout.VERTICAL);card.setPadding(dp(26),dp(24),dp(26),dp(24));
        GradientDrawable bg=new GradientDrawable();bg.setColor(0xF5181A21);bg.setCornerRadius(dp(28));bg.setStroke(dp(1),0xFF30333E);card.setBackground(bg);card.setElevation(dp(22));
        TextView tag=text("●  DeepSeek Claw · 等待你的回复",12,0xFFB19BEF);card.addView(tag);
        TextView title=text("这件事，需要你确认。",24,Color.WHITE);title.setTypeface(null,Typeface.BOLD);card.addView(title);
        card.addView(text(message.length()>600?message.substring(0,600)+"…":message,15,0xFFC5C8DD));
        card.addView(text("你可以确认已读，或给自己再留 15 分钟。",12,0xFF969BB4));
        TextView yes=text("确认已读",16,0xFF15161B);yes.setGravity(Gravity.CENTER);yes.setPadding(dp(12),dp(14),dp(12),dp(14));
        GradientDrawable button=new GradientDrawable();button.setColor(0xFFEEEEF1);button.setCornerRadius(dp(25));yes.setBackground(button);
        LinearLayout.LayoutParams bp=new LinearLayout.LayoutParams(-1,-2);bp.topMargin=dp(18);card.addView(yes,bp);
        TextView later=text("再宽限 15 分钟",14,0xFFC5C8DD);later.setGravity(Gravity.CENTER);later.setPadding(0,dp(18),0,dp(6));card.addView(later);
        yes.setOnClickListener(v->finish(id,0,yes));later.setOnClickListener(v->finish(id,15,later));
        WindowManager.LayoutParams p=new WindowManager.LayoutParams(Math.min(context.getResources().getDisplayMetrics().widthPixels-dp(32),dp(480)),-2,
            WindowManager.LayoutParams.TYPE_APPLICATION_OVERLAY,WindowManager.LayoutParams.FLAG_DIM_BEHIND|WindowManager.LayoutParams.FLAG_NOT_TOUCH_MODAL,android.graphics.PixelFormat.TRANSLUCENT);
        p.gravity=Gravity.CENTER;p.dimAmount=.65f;
        if(Build.VERSION.SDK_INT>=31){p.flags|=WindowManager.LayoutParams.FLAG_BLUR_BEHIND;p.setBlurBehindRadius(dp(20));}
        view=card;try{
            boolean motion=animate();if(motion){card.setAlpha(0);card.setTranslationY(dp(22));card.setScaleX(.98f);card.setScaleY(.98f);p.dimAmount=0;}
            manager.addView(card,p);
            if(motion){
                card.animate().alpha(1).translationY(0).scaleX(1).scaleY(1).setDuration(360).setInterpolator(new android.view.animation.PathInterpolator(.16f,1f,.3f,1f)).start();
                android.animation.ValueAnimator dim=android.animation.ValueAnimator.ofFloat(0,.65f);dim.setDuration(280);dim.addUpdateListener(a->{if(view==card){p.dimAmount=(float)a.getAnimatedValue();try{manager.updateViewLayout(card,p);}catch(Exception ignored){}}});
                dimAnimator=dim;dim.start();
            }
        }catch(Exception e){view=null;current="";}
    }
    private void finish(String id,int minutes,TextView pressed){
        if(replying)return;replying=true;
        CharSequence original=pressed.getText();pressed.setEnabled(false);pressed.setText("正在确认…");
        android.os.Handler main=new android.os.Handler(android.os.Looper.getMainLooper());
        new Thread(()->{try{
            JSONObject data=new JSONObject();if(minutes==0)data.put("ids",new JSONArray().put(id));else data.put("minutes",minutes);
            new SecureTransport(context).request("POST",minutes==0?"/api/ack":"/api/snooze",data,null);
            main.post(()->{if(id.equals(current))hideAnimated();});
        }catch(Exception error){main.post(()->{if(id.equals(current)){replying=false;pressed.setEnabled(true);pressed.setText(original);Toast.makeText(context,"暂未确认，请检查网络后重试",Toast.LENGTH_LONG).show();}});}},"pulse-overlay-reply").start();
    }
    private void hideAnimated(){
        if(dimAnimator!=null){dimAnimator.cancel();dimAnimator=null;}
        if(view==null||!animate()){hide();return;}View card=view;
        card.animate().cancel();
        card.animate().alpha(0).translationY(dp(14)).scaleX(.985f).scaleY(.985f).setDuration(180).setInterpolator(new android.view.animation.AccelerateInterpolator()).withEndAction(()->{if(view==card)hide();}).start();
    }
    void hide(){if(dimAnimator!=null){dimAnimator.cancel();dimAnimator=null;}if(view!=null){view.animate().cancel();try{manager.removeView(view);}catch(Exception ignored){}view=null;}current="";replying=false;}
}
