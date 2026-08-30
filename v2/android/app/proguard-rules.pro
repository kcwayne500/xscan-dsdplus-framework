-keepclassmembers class com.xscan.radio.WebBridge { @android.webkit.JavascriptInterface <methods>; }
-keep class org.json.** { *; }
# BouncyCastle registers Ed25519 implementations by class-name strings. R8
# cannot discover those reflective references and otherwise removes them.
-keep class org.bouncycastle.** { *; }
-dontwarn org.bouncycastle.**
