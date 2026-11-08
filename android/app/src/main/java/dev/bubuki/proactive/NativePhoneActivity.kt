package dev.bubuki.proactive

import android.content.Intent
import android.os.Bundle
import androidx.activity.ComponentActivity
import androidx.activity.compose.setContent
import androidx.compose.runtime.*
import kotlinx.coroutines.*
import org.json.JSONObject

/** Our Root workspace controls, using the same task loop and model settings as chat. */
class NativePhoneActivity : ComponentActivity() {
    private val scope = CoroutineScope(SupervisorJob() + Dispatchers.Main.immediate)
    private var poll: Job? = null
    private var busy by mutableStateOf(false)
    private var error by mutableStateOf("")
    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState); RootPhoneHarness.initialize(this)
        setContent {
            val state by RootPhoneHarness.changes.collectAsState()
            RootPhoneScreen(state, busy, error, ::command, ::settings, ::finish)
        }
    }
    override fun onResume() {
        super.onResume(); poll?.cancel()
        poll = scope.launch {
            try { withContext(Dispatchers.IO) { RootPhoneHarness.status(this@NativePhoneActivity) } } catch (failure: Exception) { if (failure is CancellationException) throw failure; error = MainActivity.safeError(failure) }
        }
    }
    private suspend fun command(op: String, arguments: JSONObject): Boolean {
        if (busy) return false
        busy = true; error = ""
        return try {
            when (op) {
                "stop" -> RootPhoneHarness.stop(this)
                "resume" -> RootPhoneHarness.resume(this)
                "notifications" -> { if (android.os.Build.VERSION.SDK_INT >= 33 && checkSelfPermission(android.Manifest.permission.POST_NOTIFICATIONS) != android.content.pm.PackageManager.PERMISSION_GRANTED) requestPermissions(arrayOf(android.Manifest.permission.POST_NOTIFICATIONS), 15)
                    else startActivity(Intent(android.provider.Settings.ACTION_APP_NOTIFICATION_SETTINGS).putExtra(android.provider.Settings.EXTRA_APP_PACKAGE, packageName)) }
                "overlay" -> startActivity(Intent(android.provider.Settings.ACTION_MANAGE_OVERLAY_PERMISSION, android.net.Uri.parse("package:$packageName")))
                "listener" -> startActivity(Intent(android.provider.Settings.ACTION_NOTIFICATION_LISTENER_SETTINGS))
                "background" -> {
                    val enabled = withContext(Dispatchers.IO) { synchronized(SecurePrefs::class.java) { val saved = SecurePrefs.read(this@NativePhoneActivity); val enabled = !saved.optBoolean("background", true); SecurePrefs.save(this@NativePhoneActivity, saved.put("background", enabled)); enabled } }
                    if (enabled) startForegroundService(Intent(this, AgentForegroundService::class.java)) else stopService(Intent(this, AgentForegroundService::class.java))
                    withContext(Dispatchers.IO) { RootPhoneHarness.status(this@NativePhoneActivity) }
                }
                else -> withContext(Dispatchers.IO) { RootPhoneHarness.command(this@NativePhoneActivity, JSONObject(arguments.toString()).put("op", op).put("manual", true)) }
            }; true
        } catch (failure: Exception) { if (failure is CancellationException) throw failure; error = MainActivity.safeError(failure); false }
        finally { busy = false }
    }
    private fun settings() {
        startActivity(
            Intent(this, NativePulseActivity::class.java)
                .addFlags(Intent.FLAG_ACTIVITY_CLEAR_TOP or Intent.FLAG_ACTIVITY_SINGLE_TOP)
                .putExtra("entryRoute", "settings")
        )
        finish()
    }
    override fun onPause() { poll?.cancel(); super.onPause() }
    override fun onRequestPermissionsResult(requestCode: Int, permissions: Array<String>, grantResults: IntArray) { super.onRequestPermissionsResult(requestCode, permissions, grantResults); scope.launch { withContext(Dispatchers.IO) { RootPhoneHarness.status(this@NativePhoneActivity) } } }
    override fun onDestroy() { scope.cancel(); super.onDestroy() }
}
