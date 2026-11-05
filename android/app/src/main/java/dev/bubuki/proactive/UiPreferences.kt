package dev.bubuki.proactive

import android.content.Context
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import org.json.JSONObject

data class UiAppearance(val appearance: String = "dark", val motion: Boolean = true, val haptics: Boolean = true)

/** Only non-secret presentation settings; API credentials remain in SecurePrefs. */
object UiPreferences {
    private var store: android.content.SharedPreferences? = null
    private val state = MutableStateFlow(UiAppearance())
    private val listener = android.content.SharedPreferences.OnSharedPreferenceChangeListener { _, _ -> refresh() }
    @JvmStatic @Synchronized fun initialize(context: Context, legacy: String? = null) {
        if (store != null) return
        val prefs = context.applicationContext.getSharedPreferences("pulse_shared_ui", Context.MODE_PRIVATE)
        if (!prefs.contains("migrated")) {
            val old = runCatching { JSONObject(legacy ?: "{}") }.getOrDefault(JSONObject())
            prefs.edit().putBoolean("migrated", true).putString("appearance", "dark")
                .putBoolean("motion", old.optBoolean("motion", true)).putBoolean("haptics", old.optBoolean("haptics", true)).commit()
        }
        if (!prefs.contains("fixed_dark_v280")) {
            prefs.edit().putBoolean("fixed_dark_v280", true).putString("appearance", "dark").commit()
        }
        store = prefs; prefs.registerOnSharedPreferenceChangeListener(listener); refresh()
    }
    private fun refresh() {
        val p = store ?: return
        val raw = p.getString("appearance", "dark") ?: "dark"
        val normalized = if (raw == "light") "light" else "dark"
        state.value = UiAppearance(normalized, p.getBoolean("motion", true), p.getBoolean("haptics", true))
    }
    fun changes(context: Context): StateFlow<UiAppearance> { initialize(context); return state.asStateFlow() }
    @JvmStatic fun update(context: Context, values: JSONObject) {
        initialize(context); val current = state.value
        val raw = values.optString("appearance", current.appearance)
        val appearance = if (raw == "light") "light" else "dark"
        store!!.edit().putString("appearance", appearance).putBoolean("motion", values.optBoolean("motion", current.motion))
            .putBoolean("haptics", values.optBoolean("haptics", current.haptics)).commit(); refresh()
    }
    @JvmStatic fun decorate(context: Context, values: JSONObject): JSONObject {
        initialize(context); val p = state.value
        return values.put("appearance", p.appearance).put("motion", p.motion).put("haptics", p.haptics)
            .put("systemDark", context.resources.configuration.uiMode and android.content.res.Configuration.UI_MODE_NIGHT_MASK == android.content.res.Configuration.UI_MODE_NIGHT_YES)
    }
}
