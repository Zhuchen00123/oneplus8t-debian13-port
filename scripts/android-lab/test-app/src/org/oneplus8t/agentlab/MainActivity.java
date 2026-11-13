package org.oneplus8t.agentlab;

import android.app.Activity;
import android.os.Bundle;
import android.view.View;
import android.widget.Button;
import android.widget.EditText;
import android.widget.TextView;

/** Owned offline fixture. No hardware, network, account, or unrelated permissions. */
public final class MainActivity extends Activity {
    @Override public void onCreate(Bundle state) {
        super.onCreate(state);
        setContentView(R.layout.main);
        final EditText input = (EditText) findViewById(R.id.input_text);
        final TextView result = (TextView) findViewById(R.id.result_text);
        ((Button) findViewById(R.id.apply_button)).setOnClickListener(new View.OnClickListener() {
            @Override public void onClick(View view) {
                result.setText(transform(input.getText().toString()));
            }
        });
    }

    /** Deliberately ordinary, unminified Java method for explicit Frida observation. */
    public String transform(String value) {
        return "OK:" + value;
    }
}
