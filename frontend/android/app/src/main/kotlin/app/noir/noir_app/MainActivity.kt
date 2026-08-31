package app.noir.noir_app

import android.content.Intent
import android.content.pm.PackageInfo
import android.content.pm.PackageManager
import android.graphics.Bitmap
import android.graphics.Canvas
import android.graphics.Rect
import android.graphics.drawable.Drawable
import android.os.Build
import android.os.Handler
import android.os.Looper
import io.flutter.embedding.android.FlutterActivity
import io.flutter.embedding.engine.FlutterEngine
import io.flutter.plugin.common.MethodChannel
import java.io.ByteArrayOutputStream
import java.io.File
import java.util.concurrent.Executors

class MainActivity : FlutterActivity() {
    private val channelName = "app.noir.noir_app/installed_apps"
    private val worker = Executors.newSingleThreadExecutor()
    private val main = Handler(Looper.getMainLooper())

    override fun configureFlutterEngine(flutterEngine: FlutterEngine) {
        super.configureFlutterEngine(flutterEngine)
        MethodChannel(flutterEngine.dartExecutor.binaryMessenger, channelName)
            .setMethodCallHandler { call, result ->
                when (call.method) {
                    "listInstalledApps" -> runAsync(result) { listInstalledApps() }
                    "extractInstalledApp" -> {
                        val packageName = call.argument<String>("packageName")
                        if (packageName.isNullOrBlank()) {
                            result.error("INVALID_PACKAGE", "Choose an application first.", null)
                        } else {
                            runAsync(result) { extractInstalledApp(packageName) }
                        }
                    }
                    else -> result.notImplemented()
                }
            }
    }

    override fun onDestroy() {
        worker.shutdownNow()
        super.onDestroy()
    }

    private fun runAsync(result: MethodChannel.Result, operation: () -> Any) {
        worker.execute {
            try {
                val value = operation()
                main.post { result.success(value) }
            } catch (error: BridgeError) {
                main.post { result.error(error.code, error.message, null) }
            } catch (_: SecurityException) {
                main.post {
                    result.error(
                        "APP_NOT_ACCESSIBLE",
                        "Android did not allow NOIR to read this installed application.",
                        null,
                    )
                }
            } catch (_: Exception) {
                main.post {
                    result.error(
                        "EXTRACTION_FAILED",
                        "NOIR could not prepare this installed application.",
                        null,
                    )
                }
            }
        }
    }

    private fun launcherApplications(): Map<String, android.content.pm.ApplicationInfo> {
        val intent = Intent(Intent.ACTION_MAIN).addCategory(Intent.CATEGORY_LAUNCHER)
        val activities = if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.TIRAMISU) {
            packageManager.queryIntentActivities(
                intent,
                PackageManager.ResolveInfoFlags.of(PackageManager.MATCH_ALL.toLong()),
            )
        } else {
            @Suppress("DEPRECATION")
            packageManager.queryIntentActivities(intent, PackageManager.MATCH_ALL)
        }
        return activities
            .mapNotNull { it.activityInfo?.applicationInfo }
            .filter { it.packageName != packageName }
            .associateBy { it.packageName }
    }

    private fun listInstalledApps(): List<Map<String, Any?>> {
        return launcherApplications().values.mapNotNull { info ->
            try {
                val packageInfo = packageInfo(info.packageName)
                mapOf(
                    "name" to packageManager.getApplicationLabel(info).toString()
                        .ifBlank { info.packageName },
                    "packageName" to info.packageName,
                    "versionName" to (packageInfo.versionName ?: ""),
                    "versionCode" to longVersionCode(packageInfo),
                    "isSplit" to !info.splitPublicSourceDirs.isNullOrEmpty(),
                    "icon" to drawablePng(packageManager.getApplicationIcon(info)),
                )
            } catch (_: Exception) {
                // An app may be removed between the launcher and metadata query.
                null
            }
        }.sortedWith(
            compareBy(String.CASE_INSENSITIVE_ORDER) { it["name"] as String },
        )
    }

    private fun extractInstalledApp(packageName: String): Map<String, Any> {
        val info = launcherApplications()[packageName]
            ?: throw BridgeError(
                "APP_NOT_VISIBLE",
                "This application is no longer available in the launcher.",
            )
        if (!info.splitPublicSourceDirs.isNullOrEmpty()) {
            throw BridgeError(
                "SPLIT_APK_UNSUPPORTED",
                "This application uses split APKs. NOIR cannot safely rebuild it yet.",
            )
        }
        val sourcePath = info.publicSourceDir
        if (sourcePath.isNullOrBlank()) {
            throw BridgeError("APP_NOT_ACCESSIBLE", "Android did not expose this APK.")
        }
        val source = File(sourcePath)
        if (!source.isFile || !source.canRead()) {
            throw BridgeError("APP_NOT_ACCESSIBLE", "The installed APK is not readable.")
        }
        val directory = File(cacheDir, "installed-apps").apply { mkdirs() }
        directory.listFiles()?.forEach { old ->
            if (old.isFile && old.name.endsWith(".apk")) old.delete()
        }
        val safePackage = packageName.replace(Regex("[^A-Za-z0-9._-]"), "_")
        val target = File(directory, "$safePackage-${System.currentTimeMillis()}.apk")
        source.inputStream().buffered().use { input ->
            target.outputStream().buffered().use { output -> input.copyTo(output) }
        }
        if (!target.isFile || target.length() != source.length()) {
            target.delete()
            throw BridgeError("EXTRACTION_FAILED", "The temporary APK copy was incomplete.")
        }
        return mapOf(
            "path" to target.absolutePath,
            "filename" to "$safePackage.apk",
            "length" to target.length(),
            "packageName" to packageName,
        )
    }

    private fun packageInfo(packageName: String): PackageInfo =
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.TIRAMISU) {
            packageManager.getPackageInfo(packageName, PackageManager.PackageInfoFlags.of(0))
        } else {
            @Suppress("DEPRECATION")
            packageManager.getPackageInfo(packageName, 0)
        }

    private fun longVersionCode(info: PackageInfo): Long =
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.P) {
            info.longVersionCode
        } else {
            @Suppress("DEPRECATION")
            info.versionCode.toLong()
        }

    private fun drawablePng(drawable: Drawable): ByteArray {
        val size = 96
        val bitmap = Bitmap.createBitmap(size, size, Bitmap.Config.ARGB_8888)
        val canvas = Canvas(bitmap)
        val previous = Rect(drawable.bounds)
        drawable.setBounds(0, 0, size, size)
        drawable.draw(canvas)
        drawable.bounds = previous
        return ByteArrayOutputStream().use { output ->
            bitmap.compress(Bitmap.CompressFormat.PNG, 90, output)
            bitmap.recycle()
            output.toByteArray()
        }
    }

    private class BridgeError(val code: String, override val message: String) : Exception(message)
}
