package dev.bubuki.proactive;
import android.app.Activity;
import android.os.Bundle;
import android.graphics.Color;
import android.view.Gravity;
import android.widget.*;
public class DisplayTestActivity extends Activity {
    @Override public void onCreate(Bundle state){super.onCreate(state);LinearLayout box=new LinearLayout(this);box.setOrientation(LinearLayout.VERTICAL);box.setGravity(Gravity.CENTER);box.setBackgroundColor(0xFF131722);
        TextView title=new TextView(this);title.setText("PULSE / 独立虚拟屏\n\n这不是你的主屏幕");title.setTextColor(Color.WHITE);title.setGravity(Gravity.CENTER);title.setTextSize(24);box.addView(title);
        Button button=new Button(this);button.setText("坐标注入测试");button.setOnClickListener(v->{button.setText("点击已到达独立虚拟屏 ✓");getSharedPreferences("display-test",0).edit().putInt("clicked",getSharedPreferences("display-test",0).getInt("clicked",0)+1).putInt("display_id",getDisplay().getDisplayId()).apply();});box.addView(button);
        FrameLayout frame=new FrameLayout(this);frame.addView(box,new FrameLayout.LayoutParams(-1,-1));
        EditText input=new EditText(this);input.setTextColor(Color.WHITE);input.setHintTextColor(0xFF9598A3);input.setHint("独立屏文字输入");input.setContentDescription("独立屏文字输入");
        input.addTextChangedListener(new android.text.TextWatcher(){public void beforeTextChanged(CharSequence s,int start,int count,int after){}public void onTextChanged(CharSequence s,int start,int before,int count){getSharedPreferences("display-test",0).edit().putString("typed",s.toString()).putInt("display_id",getDisplay().getDisplayId()).apply();}public void afterTextChanged(android.text.Editable e){}});
        FrameLayout.LayoutParams editor=new FrameLayout.LayoutParams(-1,-2,Gravity.BOTTOM);editor.setMargins(24,0,24,48);frame.addView(input,editor);setContentView(frame);}
}
