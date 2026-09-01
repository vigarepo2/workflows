package in.gov.ecourts.eCourtsServices;

import android.os.Bundle;
import android.view.View;
import androidx.core.graphics.C1148b;
import androidx.core.view.AbstractC1231a0;
import androidx.core.view.AbstractC1260o0;
import androidx.core.view.C1190C0;
import androidx.core.view.InterfaceC1199H;
import com.facebook.react.ReactActivity;
import com.facebook.react.ReactActivityDelegate;
import com.facebook.react.defaults.DefaultNewArchitectureEntryPoint;
import com.facebook.react.defaults.DefaultReactActivityDelegate;
import kotlin.Metadata;
import kotlin.jvm.internal.Intrinsics;
import p135a4.AbstractC0754c;

/* JADX INFO: loaded from: classes2.dex */
@Metadata(m15709d1 = {"\u0000$\n\u0002\u0018\u0002\n\u0002\u0018\u0002\n\u0002\b\u0003\n\u0002\u0010\u000e\n\u0000\n\u0002\u0018\u0002\n\u0000\n\u0002\u0010\u0002\n\u0000\n\u0002\u0018\u0002\n\u0000\u0018\u00002\u00020\u0001B\u0007¢\u0006\u0004\b\u0002\u0010\u0003J\b\u0010\u0004\u001a\u00020\u0005H\u0014J\b\u0010\u0006\u001a\u00020\u0007H\u0014J\u0012\u0010\b\u001a\u00020\t2\b\u0010\n\u001a\u0004\u0018\u00010\u000bH\u0014¨\u0006\f"}, m15710d2 = {"Lin/gov/ecourts/eCourtsServices/MainActivity;", "Lcom/facebook/react/ReactActivity;", "<init>", "()V", "getMainComponentName", "", "createReactActivityDelegate", "Lcom/facebook/react/ReactActivityDelegate;", "onCreate", "", "savedInstanceState", "Landroid/os/Bundle;", "app_release"}, m15711k = 1, m15712mv = {2, 0, 0}, m15714xi = 48)
public final class MainActivity extends ReactActivity {
    /* JADX INFO: Access modifiers changed from: private */
    public static final C1190C0 onCreate$lambda$0(View view, C1190C0 insets) {
        Intrinsics.checkNotNullParameter(view, "view");
        Intrinsics.checkNotNullParameter(insets, "insets");
        C1148b c1148bM5167f = insets.m5167f(C1190C0.l.m5229g());
        Intrinsics.checkNotNullExpressionValue(c1148bM5167f, "getInsets(...)");
        view.setPadding(c1148bM5167f.f6215a, c1148bM5167f.f6216b, c1148bM5167f.f6217c, c1148bM5167f.f6218d);
        return insets;
    }

    @Override // com.facebook.react.ReactActivity
    protected ReactActivityDelegate createReactActivityDelegate() {
        return new DefaultReactActivityDelegate(this, getMainComponentName(), DefaultNewArchitectureEntryPoint.getFabricEnabled());
    }

    @Override // com.facebook.react.ReactActivity
    protected String getMainComponentName() {
        return "eCourtsServicesReact";
    }

    @Override // com.facebook.react.ReactActivity, androidx.fragment.app.AbstractActivityC1473t, androidx.activity.ComponentActivity, androidx.core.app.AbstractActivityC1115e, android.app.Activity
    protected void onCreate(Bundle savedInstanceState) {
        AbstractC0754c.m2474f(this);
        super.onCreate(null);
        AbstractC1260o0.m5630b(getWindow(), false);
        View viewFindViewById = findViewById(android.R.id.content);
        Intrinsics.checkNotNullExpressionValue(viewFindViewById, "findViewById(...)");
        AbstractC1231a0.m5336C0(viewFindViewById, new InterfaceC1199H() { // from class: in.gov.ecourts.eCourtsServices.a
            @Override // androidx.core.view.InterfaceC1199H
            public final C1190C0 onApplyWindowInsets(View view, C1190C0 c1190c0) {
                return MainActivity.onCreate$lambda$0(view, c1190c0);
            }
        });
    }
}
