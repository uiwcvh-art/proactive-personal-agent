package dev.bubuki.proactive;

import android.app.Activity;
import android.content.Context;
import android.content.Intent;
import android.content.pm.PackageManager;
import android.content.pm.ResolveInfo;
import android.graphics.Color;
import android.graphics.Typeface;
import android.graphics.drawable.Drawable;
import android.graphics.drawable.GradientDrawable;
import android.os.Bundle;
import android.text.Editable;
import android.text.TextUtils;
import android.text.TextWatcher;
import android.view.Gravity;
import android.view.KeyEvent;
import android.view.MotionEvent;
import android.view.View;
import android.view.inputmethod.EditorInfo;
import android.view.inputmethod.InputMethodManager;
import android.widget.EditText;
import android.widget.FrameLayout;
import android.widget.ImageView;
import android.widget.LinearLayout;
import android.widget.ScrollView;
import android.widget.TextView;

import java.text.SimpleDateFormat;
import java.util.ArrayList;
import java.util.Collections;
import java.util.Date;
import java.util.HashMap;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Locale;
import java.util.Map;

/**
 * VirtualDisplay Home Screen ("脉冲独立屏 · 桌面").
 * Serves as the dedicated desktop launcher inside the Root VirtualDisplay (displayId > 0),
 * replacing AOSP's barebones SecondaryDisplayLauncher so both the user and the AI agent
 * see a clean 4-column app icon grid with a non-auto-launching search filter bar.
 */
public class DisplayTestActivity extends Activity {

    private static final class HomeAppEntry {
        final String label;
        final String pkg;
        final String component;
        final Drawable icon;
        final int priority;

        HomeAppEntry(String label, String pkg, String component, Drawable icon, int priority) {
            this.label = label;
            this.pkg = pkg;
            this.component = component;
            this.icon = icon;
            this.priority = priority;
        }
    }

    private static final Map<String, Integer> PRIORITY_PACKAGES = new HashMap<>();
    static {
        String[] ordered = new String[]{
            "com.tencent.mm",
            "com.eg.android.AlipayGphone",
            "com.tencent.mobileqq",
            "tv.danmaku.bili",
            "com.taobao.taobao",
            "com.sankuai.meituan",
            "com.autonavi.minimap",
            "com.xingin.xhs",
            "com.ss.android.ugc.aweme",
            "com.zhihu.android",
            "com.netease.cloudmusic",
            "com.tencent.qqmusic",
            "com.heytap.health",
            "com.android.settings",
            "com.android.chrome",
            "com.UCMobile",
            "com.quark.browser"
        };
        for (int i = 0; i < ordered.length; i++) {
            PRIORITY_PACKAGES.put(ordered[i].toLowerCase(Locale.ROOT), i + 1);
        }
    }

    private final List<HomeAppEntry> allApps = new ArrayList<>();
    private LinearLayout gridRowsContainer;
    private TextView emptyFilterText;
    private TextView countBadgeText;
    private EditText searchInput;

    private int dp(float v) {
        float density = getResources().getDisplayMetrics().density;
        return Math.round(v * (density > 0f ? density : 2f));
    }

    @Override
    protected void onCreate(Bundle state) {
        super.onCreate(state);
        setTitle("独立屏桌面");

        LinearLayout root = new LinearLayout(this);
        root.setOrientation(LinearLayout.VERTICAL);
        root.setBackgroundColor(0xFF0E121B);
        root.setFocusable(true);
        root.setFocusableInTouchMode(true);
        root.setPadding(dp(16), dp(18), dp(16), dp(14));

        // 1. Header bar (Clock + Title + Subtitle badge)
        LinearLayout header = new LinearLayout(this);
        header.setOrientation(LinearLayout.HORIZONTAL);
        header.setGravity(Gravity.CENTER_VERTICAL);
        header.setPadding(dp(4), dp(2), dp(4), dp(12));

        LinearLayout titleCol = new LinearLayout(this);
        titleCol.setOrientation(LinearLayout.VERTICAL);
        TextView titleView = new TextView(this);
        String timeStr = new SimpleDateFormat("HH:mm", Locale.getDefault()).format(new Date());
        titleView.setText("脉冲独立屏 · " + timeStr);
        titleView.setTextColor(0xFFF5F7FA);
        titleView.setTextSize(18f);
        titleView.setTypeface(Typeface.DEFAULT_BOLD);
        titleCol.addView(titleView);

        TextView subView = new TextView(this);
        subView.setText("点按图标在虚拟屏内打开应用，搜索框回车仅过滤不直接启动");
        subView.setTextColor(0xFF8F9BB3);
        subView.setTextSize(11f);
        titleCol.addView(subView);

        header.addView(titleCol, new LinearLayout.LayoutParams(0, -2, 1f));

        countBadgeText = new TextView(this);
        countBadgeText.setTextColor(0xFF94A3B8);
        countBadgeText.setTextSize(11f);
        countBadgeText.setPadding(dp(10), dp(4), dp(10), dp(4));
        GradientDrawable badgeBg = new GradientDrawable();
        badgeBg.setColor(0xFF1A2234);
        badgeBg.setCornerRadius(dp(12));
        badgeBg.setStroke(1, 0xFF2E3A52);
        countBadgeText.setBackground(badgeBg);
        header.addView(countBadgeText, new LinearLayout.LayoutParams(-2, -2));
        root.addView(header, new LinearLayout.LayoutParams(-1, -2));

        // 2. Search Bar (filters grid in real time; Enter hides IME and NEVER auto-launches an app)
        LinearLayout searchBox = new LinearLayout(this);
        searchBox.setOrientation(LinearLayout.HORIZONTAL);
        searchBox.setGravity(Gravity.CENTER_VERTICAL);
        searchBox.setPadding(dp(12), dp(6), dp(10), dp(6));
        GradientDrawable searchBg = new GradientDrawable();
        searchBg.setColor(0xFF182030);
        searchBg.setCornerRadius(dp(14));
        searchBg.setStroke(Math.max(1, dp(1)), 0xFF2E3B52);
        searchBox.setBackground(searchBg);

        TextView searchIcon = new TextView(this);
        searchIcon.setText("⌕");
        searchIcon.setTextColor(0xFF8F9BB3);
        searchIcon.setTextSize(15f);
        searchIcon.setPadding(0, 0, dp(8), 0);
        searchBox.addView(searchIcon, new LinearLayout.LayoutParams(-2, -2));

        searchInput = new EditText(this);
        searchInput.setId(android.R.id.edit);
        searchInput.setBackground(null);
        searchInput.setSingleLine(true);
        searchInput.setTextColor(Color.WHITE);
        searchInput.setHintTextColor(0xFF7B879E);
        searchInput.setTextSize(13.5f);
        searchInput.setHint("搜索应用（输入名称或包名过滤）");
        searchInput.setContentDescription("搜索应用");
        searchInput.setImeOptions(EditorInfo.IME_ACTION_SEARCH | EditorInfo.IME_FLAG_NO_EXTRACT_UI);

        final TextView clearBtn = new TextView(this);
        clearBtn.setText("清空");
        clearBtn.setTextColor(0xFF94A3B8);
        clearBtn.setTextSize(11.5f);
        clearBtn.setPadding(dp(8), dp(4), dp(8), dp(4));
        clearBtn.setVisibility(View.GONE);
        clearBtn.setClickable(true);
        clearBtn.setContentDescription("清空搜索");
        clearBtn.setOnClickListener(v -> {
            searchInput.setText("");
            root.requestFocus();
        });

        searchInput.setOnEditorActionListener((v, actionId, event) -> {
            if (actionId == EditorInfo.IME_ACTION_SEARCH
                || actionId == EditorInfo.IME_ACTION_DONE
                || (event != null && event.getKeyCode() == KeyEvent.KEYCODE_ENTER)) {
                hideKeyboard(v);
                root.requestFocus();
                return true;
            }
            return false;
        });

        searchInput.addTextChangedListener(new TextWatcher() {
            @Override public void beforeTextChanged(CharSequence s, int start, int count, int after) {}
            @Override public void onTextChanged(CharSequence s, int start, int before, int count) {
                String q = s != null ? s.toString() : "";
                clearBtn.setVisibility(q.isEmpty() ? View.GONE : View.VISIBLE);
                recordTypedPref(q);
                renderAppGrid(q);
            }
            @Override public void afterTextChanged(Editable e) {}
        });

        searchBox.addView(searchInput, new LinearLayout.LayoutParams(0, -2, 1f));
        searchBox.addView(clearBtn, new LinearLayout.LayoutParams(-2, -2));
        LinearLayout.LayoutParams searchLp = new LinearLayout.LayoutParams(-1, -2);
        searchLp.bottomMargin = dp(12);
        root.addView(searchBox, searchLp);

        // 3. Scrollable 4-column App Icon Grid
        ScrollView scroll = new ScrollView(this);
        scroll.setFillViewport(true);
        scroll.setVerticalScrollBarEnabled(false);

        FrameLayout scrollContent = new FrameLayout(this);
        gridRowsContainer = new LinearLayout(this);
        gridRowsContainer.setOrientation(LinearLayout.VERTICAL);
        scrollContent.addView(gridRowsContainer, new FrameLayout.LayoutParams(-1, -2));

        emptyFilterText = new TextView(this);
        emptyFilterText.setText("未找到匹配的应用");
        emptyFilterText.setTextColor(0xFF7B879E);
        emptyFilterText.setTextSize(13f);
        emptyFilterText.setGravity(Gravity.CENTER);
        emptyFilterText.setPadding(dp(16), dp(48), dp(16), dp(48));
        emptyFilterText.setVisibility(View.GONE);
        scrollContent.addView(emptyFilterText, new FrameLayout.LayoutParams(-1, -2, Gravity.TOP | Gravity.CENTER_HORIZONTAL));

        scroll.addView(scrollContent, new FrameLayout.LayoutParams(-1, -1));
        root.addView(scroll, new LinearLayout.LayoutParams(-1, 0, 1f));

        setContentView(root);
        root.requestFocus();

        loadAppsAndRender();
    }

    @Override
    protected void onResume() {
        super.onResume();
        if (allApps.isEmpty()) {
            loadAppsAndRender();
        }
    }

    @Override
    public boolean dispatchTouchEvent(MotionEvent ev) {
        if (ev != null && ev.getActionMasked() == MotionEvent.ACTION_UP) {
            recordClickPref();
        }
        return super.dispatchTouchEvent(ev);
    }

    private void recordClickPref() {
        try {
            int dispId = getDisplay() != null ? getDisplay().getDisplayId() : 0;
            int prev = getSharedPreferences("display-test", 0).getInt("clicked", 0);
            getSharedPreferences("display-test", 0).edit()
                .putInt("clicked", prev + 1)
                .putInt("display_id", dispId)
                .apply();
        } catch (Throwable ignored) {}
    }

    private void recordTypedPref(String text) {
        try {
            int dispId = getDisplay() != null ? getDisplay().getDisplayId() : 0;
            getSharedPreferences("display-test", 0).edit()
                .putString("typed", text)
                .putInt("display_id", dispId)
                .apply();
        } catch (Throwable ignored) {}
    }

    private void hideKeyboard(View v) {
        try {
            InputMethodManager imm = (InputMethodManager) getSystemService(Context.INPUT_METHOD_SERVICE);
            if (imm != null && v != null) {
                imm.hideSoftInputFromWindow(v.getWindowToken(), 0);
            }
        } catch (Throwable ignored) {}
    }

    private void loadAppsAndRender() {
        new Thread(() -> {
            List<HomeAppEntry> loaded = queryInstalledLauncherApps();
            runOnUiThread(() -> {
                allApps.clear();
                allApps.addAll(loaded);
                String curQuery = searchInput != null && searchInput.getText() != null
                    ? searchInput.getText().toString()
                    : "";
                renderAppGrid(curQuery);
            });
        }, "pulse-vd-home-loader").start();
    }

    private List<HomeAppEntry> queryInstalledLauncherApps() {
        PackageManager pm = getPackageManager();
        Intent mainIntent = new Intent(Intent.ACTION_MAIN, null);
        mainIntent.addCategory(Intent.CATEGORY_LAUNCHER);
        List<ResolveInfo> rawList = pm.queryIntentActivities(mainIntent, 0);
        String myPkg = getPackageName();
        Map<String, HomeAppEntry> byComponent = new LinkedHashMap<>();
        if (rawList != null) {
            for (ResolveInfo ri : rawList) {
                if (ri == null || ri.activityInfo == null) continue;
                String pkg = ri.activityInfo.packageName;
                String cls = ri.activityInfo.name;
                if (pkg == null || cls == null || pkg.equals(myPkg)) continue;
                if (pkg.equals("com.android.launcher") || cls.contains("SecondaryDisplayLauncher")) continue;
                String comp = pkg + "/" + cls;
                if (byComponent.containsKey(comp)) continue;
                String label = "";
                try {
                    CharSequence cs = ri.loadLabel(pm);
                    if (cs != null) label = cs.toString().trim();
                } catch (Throwable ignored) {}
                if (label.isEmpty()) label = pkg;
                Drawable icon = null;
                try {
                    icon = ri.loadIcon(pm);
                } catch (Throwable ignored) {}
                Integer prio = PRIORITY_PACKAGES.get(pkg.toLowerCase(Locale.ROOT));
                int pVal = prio != null ? prio : 999;
                byComponent.put(comp, new HomeAppEntry(label, pkg, comp, icon, pVal));
            }
        }
        List<HomeAppEntry> result = new ArrayList<>(byComponent.values());
        Collections.sort(result, (a, b) -> {
            if (a.priority != b.priority) return Integer.compare(a.priority, b.priority);
            return a.label.compareToIgnoreCase(b.label);
        });
        return result;
    }

    private void renderAppGrid(String rawQuery) {
        if (gridRowsContainer == null) return;
        gridRowsContainer.removeAllViews();
        String q = rawQuery != null ? rawQuery.trim().toLowerCase(Locale.ROOT) : "";
        List<HomeAppEntry> filtered = new ArrayList<>();
        for (HomeAppEntry item : allApps) {
            if (q.isEmpty()
                || item.label.toLowerCase(Locale.ROOT).contains(q)
                || item.pkg.toLowerCase(Locale.ROOT).contains(q)) {
                filtered.add(item);
            }
        }
        if (countBadgeText != null) {
            countBadgeText.setText(filtered.size() + " 个应用");
        }
        if (emptyFilterText != null) {
            emptyFilterText.setVisibility(filtered.isEmpty() ? View.VISIBLE : View.GONE);
        }
        int cols = 4;
        int total = filtered.size();
        for (int i = 0; i < total; i += cols) {
            LinearLayout row = new LinearLayout(this);
            row.setOrientation(LinearLayout.HORIZONTAL);
            row.setWeightSum(cols);
            LinearLayout.LayoutParams rowLp = new LinearLayout.LayoutParams(-1, -2);
            rowLp.bottomMargin = dp(10);
            for (int c = 0; c < cols; c++) {
                int idx = i + c;
                if (idx < total) {
                    View cell = createAppCell(filtered.get(idx));
                    row.addView(cell, new LinearLayout.LayoutParams(0, -2, 1f));
                } else {
                    View spacer = new View(this);
                    row.addView(spacer, new LinearLayout.LayoutParams(0, dp(1), 1f));
                }
            }
            gridRowsContainer.addView(row, rowLp);
        }
    }

    private View createAppCell(final HomeAppEntry app) {
        LinearLayout cell = new LinearLayout(this);
        cell.setOrientation(LinearLayout.VERTICAL);
        cell.setGravity(Gravity.CENTER_HORIZONTAL);
        cell.setPadding(dp(4), dp(8), dp(4), dp(8));
        cell.setClickable(true);
        cell.setFocusable(true);
        cell.setContentDescription(app.label);

        FrameLayout iconWrap = new FrameLayout(this);
        GradientDrawable squircle = new GradientDrawable();
        squircle.setColor(0xFF1B2436);
        squircle.setCornerRadius(dp(15));
        squircle.setStroke(1, 0xFF2B3850);
        iconWrap.setBackground(squircle);
        int wrapSize = dp(52);

        if (app.icon != null) {
            ImageView iv = new ImageView(this);
            iv.setImageDrawable(app.icon);
            iv.setScaleType(ImageView.ScaleType.FIT_CENTER);
            int iconSize = dp(38);
            iconWrap.addView(iv, new FrameLayout.LayoutParams(iconSize, iconSize, Gravity.CENTER));
        } else {
            TextView fallback = new TextView(this);
            fallback.setText(app.label.isEmpty() ? "App" : app.label.substring(0, 1));
            fallback.setTextColor(Color.WHITE);
            fallback.setTextSize(18f);
            fallback.setTypeface(Typeface.DEFAULT_BOLD);
            fallback.setGravity(Gravity.CENTER);
            iconWrap.addView(fallback, new FrameLayout.LayoutParams(-1, -1, Gravity.CENTER));
        }
        cell.addView(iconWrap, new LinearLayout.LayoutParams(wrapSize, wrapSize));

        TextView labelView = new TextView(this);
        labelView.setText(app.label);
        labelView.setTextColor(0xFFE2E8F0);
        labelView.setTextSize(11.5f);
        labelView.setSingleLine(true);
        labelView.setEllipsize(TextUtils.TruncateAt.END);
        labelView.setGravity(Gravity.CENTER);
        LinearLayout.LayoutParams labelLp = new LinearLayout.LayoutParams(-1, -2);
        labelLp.topMargin = dp(5);
        cell.addView(labelView, labelLp);

        cell.setOnClickListener(v -> {
            recordClickPref();
            hideKeyboard(v);
            final int dispId = getDisplay() != null ? getDisplay().getDisplayId() : -1;
            final Context appCtx = getApplicationContext();
            new Thread(() -> {
                try {
                    RootPhoneHarness.launchFromVirtualHome(appCtx, dispId, app.component);
                } catch (Throwable ignored) {}
            }, "pulse-vd-home-launch").start();
        });
        return cell;
    }
}
