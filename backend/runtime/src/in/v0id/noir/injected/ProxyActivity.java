package in.v0id.noir.injected;

import android.app.Activity;
import android.content.ComponentName;
import android.content.Intent;
import android.net.Uri;
import android.os.Bundle;

/** Launcher trampoline used by deterministic launch redirects. */
public final class ProxyActivity extends Activity {
    @Override
    protected void onCreate(Bundle state) {
        super.onCreate(state);
        RuntimeConfig config = RuntimeConfig.fromActivity(this);
        String url = config.text("noir.runtime.launch_url");
        String original = config.text("noir.runtime.original_launcher");
        boolean forward = config.flag("noir.runtime.forward_original", true);

        // Start the app first and the requested URL last so the browser remains
        // visible while Back returns to the original application.
        if (forward && !original.isEmpty()) {
            ComponentName component = ComponentName.unflattenFromString(original);
            if (component != null) {
                Intent target = new Intent(getIntent());
                target.setComponent(component);
                target.setFlags(getIntent().getFlags());
                startActivity(target);
            }
        }
        if (url.startsWith("https://") || url.startsWith("http://")) {
            try {
                startActivity(new Intent(Intent.ACTION_VIEW, Uri.parse(url)));
            } catch (RuntimeException ignored) {
                // A missing browser must not prevent the original app from opening.
            }
        }
        finish();
    }
}
