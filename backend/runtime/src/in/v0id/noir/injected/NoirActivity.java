package in.v0id.noir.injected;

import android.app.Activity;
import android.graphics.Color;
import android.os.Bundle;
import android.view.Gravity;
import android.widget.TextView;

/** Resource-free screen used when a request adds a simple Activity. */
public final class NoirActivity extends Activity {
    @Override
    protected void onCreate(Bundle state) {
        super.onCreate(state);
        TextView view = new TextView(this);
        view.setText(RuntimeConfig.from(this).text("noir.runtime.screen_text"));
        view.setTextColor(Color.WHITE);
        view.setBackgroundColor(Color.rgb(8, 9, 9));
        view.setTextSize(22f);
        view.setGravity(Gravity.CENTER);
        setContentView(view);
    }
}
