package com.xscan.radio

import android.Manifest
import android.annotation.SuppressLint
import android.content.Intent
import android.content.pm.PackageManager
import android.net.Uri
import android.os.Bundle
import android.webkit.JavascriptInterface
import android.webkit.WebChromeClient
import android.webkit.WebResourceRequest
import android.webkit.WebView
import android.webkit.WebViewClient
import androidx.activity.OnBackPressedCallback
import androidx.appcompat.app.AppCompatActivity
import androidx.core.app.ActivityCompat
import org.json.JSONObject

class MainActivity : AppCompatActivity() {
    private lateinit var webView: WebView
    private val prefs by lazy { getSharedPreferences("xscan", MODE_PRIVATE) }
    private fun allowed(uri: Uri): Boolean {
        val hosts = listOfNotNull(Uri.parse(BuildConfig.DEFAULT_PUBLIC_URL).host, prefs.getString("public_url",null)?.let { Uri.parse(it).host })
        return uri.scheme == "https" && uri.host in hosts
    }

    @SuppressLint("SetJavaScriptEnabled", "JavascriptInterface")
    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        if (android.os.Build.VERSION.SDK_INT >= 33 && ActivityCompat.checkSelfPermission(this, Manifest.permission.POST_NOTIFICATIONS) != PackageManager.PERMISSION_GRANTED) ActivityCompat.requestPermissions(this,arrayOf(Manifest.permission.POST_NOTIFICATIONS),8)
        webView=WebView(this); setContentView(webView)
        webView.settings.javaScriptEnabled=true; webView.settings.domStorageEnabled=true; webView.settings.mediaPlaybackRequiresUserGesture=true
        webView.settings.allowFileAccess=false; webView.settings.allowContentAccess=false; webView.settings.setSupportMultipleWindows(false)
        webView.webChromeClient=WebChromeClient()
        webView.webViewClient=object:WebViewClient(){
            override fun shouldOverrideUrlLoading(view:WebView,request:WebResourceRequest):Boolean { if(allowed(request.url)) return false; startActivity(Intent(Intent.ACTION_VIEW,request.url)); return true }
        }
        webView.addJavascriptInterface(WebBridge(this),"XScanAndroid")
        onBackPressedDispatcher.addCallback(this,object:OnBackPressedCallback(true){override fun handleOnBackPressed(){if(webView.canGoBack())webView.goBack() else finish()}})
        val url=prefs.getString("public_url",null) ?: BuildConfig.DEFAULT_PUBLIC_URL
        webView.loadUrl(url.trimEnd('/')+"/#dashboard")
    }
}

class WebBridge(private val activity: MainActivity) {
    @JavascriptInterface fun getPublicKey():String = DeviceIdentity.publicKey(activity)
    @JavascriptInterface fun getPublicKeyResult():String = try {
        JSONObject().put("ok",true).put("public_key",DeviceIdentity.publicKey(activity)).toString()
    } catch(error:Exception) {
        JSONObject().put("ok",false).put("error","${error.javaClass.simpleName}: ${error.message ?: "device key generation failed"}").toString()
    }
    @JavascriptInterface fun isPaired():Boolean = DeviceIdentity.isRegistered(activity)
    @JavascriptInterface fun setDeviceRegistration(json:String) { val value=JSONObject(json); DeviceIdentity.saveRegistration(activity,value.getString("id"),value.optString("public_url",BuildConfig.DEFAULT_PUBLIC_URL),value.optString("hls_path")) }
    @JavascriptInterface fun listenLive():String {
        if (!isPaired()) return "pair_required"
        activity.startForegroundService(Intent(activity,PlaybackService::class.java).setAction(PlaybackService.ACTION_PLAY))
        return "connecting"
    }
    @JavascriptInterface fun togglePlayback():String {
        if (!isPaired()) return "pair_required"
        activity.startService(Intent(activity,PlaybackService::class.java).setAction(PlaybackService.ACTION_TOGGLE))
        return "connecting"
    }
    @JavascriptInterface fun stopPlayback() { activity.startService(Intent(activity,PlaybackService::class.java).setAction(PlaybackService.ACTION_STOP)) }
    @JavascriptInterface fun getPlaybackStatus():String = activity.getSharedPreferences("xscan",AppCompatActivity.MODE_PRIVATE).getString("playback_status","{\"state\":\"idle\"}")!!
}
