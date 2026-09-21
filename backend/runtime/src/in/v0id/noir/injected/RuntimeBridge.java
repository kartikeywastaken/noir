package in.v0id.noir.injected;

import android.app.Activity;
import android.app.Application;
import android.os.Bundle;
import android.view.MotionEvent;
import android.view.Window;
import android.widget.Toast;

import java.lang.reflect.InvocationHandler;
import java.lang.reflect.InvocationTargetException;
import java.lang.reflect.Method;
import java.lang.reflect.Proxy;
import java.util.WeakHashMap;

/** Stable bridge target; original Smali only needs to call install(Activity). */
public final class RuntimeBridge {
    private static final WeakHashMap<Activity, Boolean> INSTALLED = new WeakHashMap<>();

    private RuntimeBridge() {}

    public static void register(Application application) {
        application.registerActivityLifecycleCallbacks(
                new Application.ActivityLifecycleCallbacks() {
                    @Override public void onActivityResumed(Activity activity) { install(activity); }
                    @Override public void onActivityCreated(Activity activity, Bundle state) {}
                    @Override public void onActivityStarted(Activity activity) {}
                    @Override public void onActivityPaused(Activity activity) {}
                    @Override public void onActivityStopped(Activity activity) {}
                    @Override public void onActivitySaveInstanceState(Activity activity, Bundle state) {}
                    @Override public void onActivityDestroyed(Activity activity) { INSTALLED.remove(activity); }
                });
    }

    public static synchronized void install(Activity activity) {
        if (activity == null || INSTALLED.containsKey(activity)) return;
        final String message = RuntimeConfig.from(activity).text("noir.runtime.interaction_toast");
        if (message.isEmpty()) return;
        final Window window = activity.getWindow();
        final Window.Callback delegate = window.getCallback();
        if (delegate == null) return;
        InvocationHandler handler = new InvocationHandler() {
            @Override
            public Object invoke(Object proxy, Method method, Object[] args) throws Throwable {
                if ("dispatchTouchEvent".equals(method.getName())
                        && args != null && args.length > 0
                        && args[0] instanceof MotionEvent
                        && ((MotionEvent) args[0]).getActionMasked() == MotionEvent.ACTION_UP) {
                    Toast.makeText(activity, message, Toast.LENGTH_SHORT).show();
                }
                try {
                    return method.invoke(delegate, args);
                } catch (InvocationTargetException error) {
                    // Preserve Window.Callback's original exception semantics. Letting
                    // InvocationTargetException escape a dynamic proxy can turn an app
                    // exception into an unrelated UndeclaredThrowableException.
                    throw error.getCause();
                }
            }
        };
        Window.Callback callback = (Window.Callback) Proxy.newProxyInstance(
                delegate.getClass().getClassLoader(),
                new Class<?>[]{Window.Callback.class},
                handler);
        window.setCallback(callback);
        INSTALLED.put(activity, Boolean.TRUE);
    }
}
