package in.v0id.noir.injected;

import android.content.ContentProvider;
import android.content.ContentValues;
import android.app.Application;
import android.database.Cursor;
import android.net.Uri;
import android.widget.Toast;

/** Optional manifest-only initialization hook for startup messages. */
public final class InitProvider extends ContentProvider {
    @Override
    public boolean onCreate() {
        if (getContext() == null) return true;
        RuntimeConfig config = RuntimeConfig.from(getContext());
        String message = config.text("noir.runtime.startup_message");
        if (!message.isEmpty()) Toast.makeText(getContext(), message, Toast.LENGTH_SHORT).show();
        if (getContext().getApplicationContext() instanceof Application) {
            RuntimeBridge.register((Application) getContext().getApplicationContext());
        }
        return true;
    }

    @Override public Cursor query(Uri uri, String[] p, String s, String[] a, String sort) { return null; }
    @Override public String getType(Uri uri) { return null; }
    @Override public Uri insert(Uri uri, ContentValues values) { return null; }
    @Override public int delete(Uri uri, String s, String[] a) { return 0; }
    @Override public int update(Uri uri, ContentValues values, String s, String[] a) { return 0; }
}
