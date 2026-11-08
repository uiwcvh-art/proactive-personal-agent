package dev.bubuki.proactive

import android.app.*
import android.content.Intent
import android.content.pm.ServiceInfo
import android.os.Build
import android.os.IBinder
import android.os.PowerManager
import kotlinx.coroutines.*

/** Keeps only a user-started phone task alive; no overlay or third-party activity. */
class RootPhoneService : Service() {
    private val scope = CoroutineScope(SupervisorJob() + Dispatchers.Main.immediate)
    private var awake: PowerManager.WakeLock? = null
    override fun onCreate() {
        super.onCreate()
        val notifications = getSystemService(NotificationManager::class.java)
        notifications.createNotificationChannel(NotificationChannel("root-workspace", "手机工作区", NotificationManager.IMPORTANCE_LOW))
        promoteToForeground()
        scope.launch {
            var seenActive = false
            RootPhoneHarness.changes.collect { state ->
                if (state.active) seenActive = true
                if (seenActive && !state.active && state.phase != "running" && state.phase != "waiting_user") {
                    if (awake?.isHeld == true) awake?.release()
                    awake = null
                    runCatching { stopForeground(STOP_FOREGROUND_REMOVE) }
                    stopSelf()
                    return@collect
                }
                runCatching { notifications.notify(18192, notification()) }
                if (state.phase == "running" && awake?.isHeld != true) {
                    awake = getSystemService(PowerManager::class.java).newWakeLock(PowerManager.PARTIAL_WAKE_LOCK, "$packageName:phone-task").apply { acquire(45 * 60 * 1000L) }
                } else if (state.phase != "running") { if (awake?.isHeld == true) awake?.release(); awake = null }
            }
        }
    }
    private fun promoteToForeground() {
        runCatching {
            val notif = notification()
            if (Build.VERSION.SDK_INT >= 34) {
                startForeground(18192, notif, ServiceInfo.FOREGROUND_SERVICE_TYPE_SPECIAL_USE)
            } else {
                startForeground(18192, notif)
            }
        }
    }
    private fun notification(): Notification {
        val state = RootPhoneHarness.changes.value
        val view = Intent(this, NativePulseActivity::class.java).addFlags(Intent.FLAG_ACTIVITY_NEW_TASK or Intent.FLAG_ACTIVITY_SINGLE_TOP).putExtra("openBot", state.botId)
        val stop = Intent(this, RootPhoneService::class.java).setAction("stop")
        return Notification.Builder(this, "root-workspace").setSmallIcon(android.R.drawable.ic_menu_view)
            .setContentTitle(if (state.phase == "waiting_user") "手机工作区需要你处理" else "手机工作区正在执行")
            .setContentText("点击查看执行记录，或随时停止")
            .setContentIntent(PendingIntent.getActivity(this, 18192, view, PendingIntent.FLAG_UPDATE_CURRENT or PendingIntent.FLAG_IMMUTABLE))
            .addAction(Notification.Action.Builder(null, "停止", PendingIntent.getService(this, 18193, stop, PendingIntent.FLAG_UPDATE_CURRENT or PendingIntent.FLAG_IMMUTABLE)).build())
            .setVisibility(Notification.VISIBILITY_PRIVATE).setOngoing(true).build()
    }
    override fun onStartCommand(intent: Intent?, flags: Int, startId: Int): Int {
        promoteToForeground()
        if (intent?.action == "stop") RootPhoneHarness.stop(this)
        return START_NOT_STICKY
    }
    override fun onDestroy() { scope.cancel(); if (awake?.isHeld == true) awake?.release(); awake = null; if (RootPhoneHarness.changes.value.active) RootPhoneHarness.stop(this); super.onDestroy() }
    override fun onBind(intent: Intent?): IBinder? = null
}
