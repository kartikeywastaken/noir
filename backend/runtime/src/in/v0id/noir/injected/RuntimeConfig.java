package in.v0id.noir.injected;

import android.content.Context;
import android.app.Activity;
import android.content.pm.ApplicationInfo;
import android.content.pm.PackageManager;
import android.os.Bundle;

final class RuntimeConfig {
    private final Bundle values;

    private RuntimeConfig(Bundle values) {
        this.values = values == null ? new Bundle() : values;
    }

    static RuntimeConfig from(Context context) {
        try {
            ApplicationInfo info = context.getPackageManager().getApplicationInfo(
                    context.getPackageName(), PackageManager.GET_META_DATA);
            return new RuntimeConfig(info.metaData);
        } catch (Exception ignored) {
            return new RuntimeConfig(null);
        }
    }

    static RuntimeConfig fromActivity(Activity activity) {
        try {
            return new RuntimeConfig(
                    activity.getPackageManager()
                            .getActivityInfo(activity.getIntent().getComponent(), PackageManager.GET_META_DATA)
                            .metaData);
        } catch (Exception ignored) {
            return from(activity);
        }
    }

    String text(String name) {
        return values.getString(name, "");
    }

    boolean flag(String name, boolean fallback) {
        return values.getBoolean(name, fallback);
    }
}
