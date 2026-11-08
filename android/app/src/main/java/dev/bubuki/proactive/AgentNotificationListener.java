package dev.bubuki.proactive;

import android.app.Notification;
import android.content.Context;
import android.content.Intent;
import android.content.pm.ApplicationInfo;
import android.content.pm.PackageManager;
import android.os.Bundle;
import android.service.notification.NotificationListenerService;
import android.service.notification.StatusBarNotification;
import java.text.SimpleDateFormat;
import java.util.ArrayList;
import java.util.Collections;
import java.util.Date;
import java.util. LinkedHashMap;
import java.util.List;
import java.util.Locale;
import java.util.Map;
import org.json.JSONArray;
import org.json.JSONObject;

/**
 * Zero-Battery-Drain Passive In-Memory Notification Ring Buffer.
 * - Never performs network I/O, disk I/O, or wake locks when notifications arrive.
 * - Only stores the most recent 80 notifications in memory (< 32 KB RAM).
 * - Queried strictly on-demand when the AI invokes `phone_workspace(op="notifications")`.
 */
public class AgentNotificationListener extends NotificationListenerService {
    private static final int MAX_BUFFER = 80;
    private static volatile AgentNotificationListener instance = null;
    private static final Map<String, String> appNameCache = new java.util.concurrent.ConcurrentHashMap<>();
    private static final Map<String, JSONObject> recentBuffer = Collections.synchronizedMap(
        new LinkedHashMap<String, JSONObject>(MAX_BUFFER + 16, 0.75f, false) {
            @Override
            protected boolean removeEldestEntry(Map.Entry<String, JSONObject> eldest) {
                return size() > MAX_BUFFER;
            }
        }
    );

    public static boolean isConnected() {
        return instance != null;
    }

    @Override
    public void onListenerConnected() {
        instance = this;
        try {
            StatusBarNotification[] active = getActiveNotifications();
            if (active != null) {
                for (StatusBarNotification sbn : active) {
                    recordPassive(sbn, true);
                }
            }
        } catch (Throwable ignored) {}
        sendBroadcast(new Intent(getPackageName() + ".UPDATE").setPackage(getPackageName()));
    }

    @Override
    public void onListenerDisconnected() {
        if (instance == this) {
            instance = null;
        }
        super.onListenerDisconnected();
    }

    @Override
    public void onNotificationPosted(StatusBarNotification sbn) {
        recordPassive(sbn, true);
        if (sbn != null && sbn.getPackageName().equals(getPackageName())) {
            sendBroadcast(new Intent(getPackageName() + ".UPDATE").setPackage(getPackageName()));
        }
    }

    @Override
    public void onNotificationRemoved(StatusBarNotification sbn) {
        if (sbn == null) return;
        String sbnKey = sbn.getKey() != null ? sbn.getKey() : (sbn.getPackageName() + ":" + sbn.getId());
        synchronized (recentBuffer) {
            for (JSONObject item : recentBuffer.values()) {
                if (sbnKey.equals(item.optString("sbn_key"))) {
                    try {
                        item.put("active", false);
                    } catch (Exception ignored) {}
                }
            }
        }
    }

    private void recordPassive(StatusBarNotification sbn, boolean isActive) {
        if (sbn == null) return;
        try {
            String pkg = sbn.getPackageName();
            if (pkg == null || pkg.equals(getPackageName()) || pkg.equals("android") || pkg.equals("com.android.systemui")) {
                return;
            }
            Notification notif = sbn.getNotification();
            if (notif == null) return;
            Bundle extras = notif.extras;
            if (extras == null) return;
            if (sbn.isOngoing() && (extras.containsKey(Notification.EXTRA_PROGRESS) || (notif.flags & Notification.FLAG_FOREGROUND_SERVICE) != 0)) {
                return;
            }

            CharSequence titleCs = extras.getCharSequence(Notification.EXTRA_TITLE);
            CharSequence textCs = extras.getCharSequence(Notification.EXTRA_TEXT);
            CharSequence bigTextCs = extras.getCharSequence(Notification.EXTRA_BIG_TEXT);
            CharSequence subTextCs = extras.getCharSequence(Notification.EXTRA_SUB_TEXT);

            String title = titleCs != null ? titleCs.toString().trim() : "";
            String text = bigTextCs != null && bigTextCs.length() > 0
                ? bigTextCs.toString().trim()
                : (textCs != null ? textCs.toString().trim() : "");
            String subText = subTextCs != null ? subTextCs.toString().trim() : "";

            if (title.isEmpty() && text.isEmpty()) return;

            String sbnKey = sbn.getKey() != null ? sbn.getKey() : (pkg + ":" + sbn.getId());
            String dedupKey = pkg + "|" + title + "|" + text;
            long postTime = sbn.getPostTime() > 0 ? sbn.getPostTime() : System.currentTimeMillis();
            synchronized (recentBuffer) {
                JSONObject existing = recentBuffer.get(dedupKey);
                if (existing != null && Math.abs(postTime - existing.optLong("timestamp_ms", 0L)) < 10_000L) {
                    existing.put("active", isActive);
                    existing.put("sbn_key", sbnKey);
                    return;
                }
            }

            String appName = resolveAppName(this, pkg);
            String timeStr = new SimpleDateFormat("MM-dd HH:mm:ss", Locale.CHINA).format(new Date(postTime));

            JSONObject item = new JSONObject();
            item.put("sbn_key", sbnKey);
            item.put("package", pkg);
            item.put("app", appName);
            item.put("title", title);
            item.put("text", text.length() > 600 ? text.substring(0, 600) : text);
            if (!subText.isEmpty()) item.put("sub_text", subText);
            item.put("time", timeStr);
            item.put("timestamp_ms", postTime);
            item.put("ongoing", sbn.isOngoing());
            item.put("active", isActive);

            synchronized (recentBuffer) {
                recentBuffer.remove(dedupKey);
                recentBuffer.put(dedupKey, item);
            }
        } catch (Throwable ignored) {}
    }

    public static String resolveAppName(Context ctx, String pkg) {
        if (pkg == null || pkg.isEmpty()) return "";
        String cached = appNameCache.get(pkg);
        if (cached != null) return cached;
        try {
            PackageManager pm = ctx.getPackageManager();
            ApplicationInfo ai = pm.getApplicationInfo(pkg, 0);
            CharSequence label = pm.getApplicationLabel(ai);
            if (label != null && label.length() > 0) {
                String resolved = label.toString();
                appNameCache.put(pkg, resolved);
                return resolved;
            }
        } catch (Throwable ignored) {}
        appNameCache.put(pkg, pkg);
        return pkg;
    }

    public static JSONArray getRecentNotifications(Context ctx, String filterQuery, int limit) {
        AgentNotificationListener svc = instance;
        if (svc != null) {
            try {
                StatusBarNotification[] active = svc.getActiveNotifications();
                if (active != null) {
                    for (StatusBarNotification sbn : active) {
                        svc.recordPassive(sbn, true);
                    }
                }
            } catch (Throwable ignored) {}
        }

        List<JSONObject> snapshot;
        synchronized (recentBuffer) {
            snapshot = new ArrayList<>(recentBuffer.values());
        }
        Collections.reverse(snapshot);

        String q = filterQuery != null ? filterQuery.trim().toLowerCase(Locale.ROOT) : "";
        int cap = Math.max(1, Math.min(80, limit > 0 ? limit : 25));
        JSONArray out = new JSONArray();
        for (JSONObject obj : snapshot) {
            if (!q.isEmpty()) {
                String hay = (obj.optString("app") + " " + obj.optString("package") + " " + obj.optString("title") + " " + obj.optString("text")).toLowerCase(Locale.ROOT);
                if (!hay.contains(q)) continue;
            }
            out.put(obj);
            if (out.length() >= cap) break;
        }
        return out;
    }
}
