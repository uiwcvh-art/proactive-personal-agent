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
        android.util.DisplayMetrics dm=context.getResources().getDisplayMetrics();
        int maxScrollH=Math.max(dp(140),(int)(dm.heightPixels*0.34f));
        LinearLayout card=new LinearLayout(context);card.setOrientation(LinearLayout.VERTICAL);card.setPadding(dp(24),dp(20),dp(24),dp(20));
        GradientDrawable bg=new GradientDrawable();bg.setColor(0xF5181A21);bg.setCornerRadius(dp(28));bg.setStroke(dp(1),0xFF30333E);card.setBackground(bg);card.setElevation(dp(22));

        LinearLayout headerRow=new LinearLayout(context);headerRow.setOrientation(LinearLayout.HORIZONTAL);headerRow.setGravity(Gravity.CENTER_VERTICAL);
        TextView tag=text("●  DeepSeek Claw · 等待你的回复",12,0xFFB19BEF);
        headerRow.addView(tag,new LinearLayout.LayoutParams(0,-2,1f));
        TextView closeBtn=text("✕",16,0xFF969BB4);closeBtn.setPadding(dp(10),dp(4),dp(4),dp(4));
        closeBtn.setOnClickListener(v->finish(id,0,closeBtn));
        headerRow.addView(closeBtn);
        card.addView(headerRow);

        TextView title=text("这件事，需要你确认。",20,Color.WHITE);title.setTypeface(null,Typeface.BOLD);card.addView(title);

        ScrollView scroll=new ScrollView(context);
        scroll.setVerticalScrollBarEnabled(true);
        scroll.setScrollbarFadingEnabled(false);
        TextView bodyTv=text(message,15,0xFFC5C8DD);
        bodyTv.setLineSpacing(dp(3),1.0f);
        scroll.addView(bodyTv,new ScrollView.LayoutParams(-1,-2));
        int estLines=Math.max(1,message.length()/20+message.split("\n").length);
        int scrollH=estLines>7?maxScrollH:ViewGroup.LayoutParams.WRAP_CONTENT;
        LinearLayout.LayoutParams sp=new LinearLayout.LayoutParams(-1,scrollH);
        sp.topMargin=dp(4);sp.bottomMargin=dp(6);
        card.addView(scroll,sp);

        card.addView(text("可上下滑动查看全文 · 确认已读或延后 15 分钟",12,0xFF969BB4));
        TextView yes=text("确认已读",16,0xFF15161B);yes.setGravity(Gravity.CENTER);yes.setPadding(dp(12),dp(13),dp(12),dp(13));
        GradientDrawable button=new GradientDrawable();button.setColor(0xFFEEEEF1);button.setCornerRadius(dp(25));yes.setBackground(button);
        LinearLayout.LayoutParams bp=new LinearLayout.LayoutParams(-1,-2);bp.topMargin=dp(12);card.addView(yes,bp);
        TextView later=text("再宽限 15 分钟",14,0xFFC5C8DD);later.setGravity(Gravity.CENTER);later.setPadding(0,dp(14),0,dp(4));card.addView(later);
        yes.setOnClickListener(v->finish(id,0,yes));later.setOnClickListener(v->finish(id,15,later));
        WindowManager.LayoutParams p=new WindowManager.LayoutParams(Math.min(dm.widthPixels-dp(32),dp(480)),-2,
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
        hideAnimated();
        new Thread(()->{try{
            JSONObject data=new JSONObject();if(minutes==0)data.put("ids",new JSONArray().put(id));else data.put("minutes",minutes);
            new SecureTransport(context).request("POST",minutes==0?"/api/ack":"/api/snooze",data,null);
        }catch(Exception ignored){}},"pulse-overlay-reply").start();
    }
    private void hideAnimated(){
        if(dimAnimator!=null){dimAnimator.cancel();dimAnimator=null;}
        if(view==null||!animate()){hide();return;}View card=view;
        card.animate().cancel();
        card.animate().alpha(0).translationY(dp(14)).scaleX(.985f).scaleY(.985f).setDuration(180).setInterpolator(new android.view.animation.AccelerateInterpolator()).withEndAction(()->{if(view==card)hide();}).start();
    }
    void hide(){if(dimAnimator!=null){dimAnimator.cancel();dimAnimator=null;}if(view!=null){view.animate().cancel();try{manager.removeView(view);}catch(Exception ignored){}view=null;}current="";replying=false;}
}
