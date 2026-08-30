package com.xscan.radio

import android.content.Context
import android.content.Intent
import android.media.AudioManager
import android.net.Uri
import android.net.wifi.WifiManager
import android.os.Handler
import android.os.Looper
import androidx.core.content.getSystemService
import androidx.media3.common.AudioAttributes
import androidx.media3.common.C
import androidx.media3.common.MediaItem
import androidx.media3.common.Player
import androidx.media3.datasource.DefaultHttpDataSource
import androidx.media3.exoplayer.ExoPlayer
import androidx.media3.exoplayer.hls.HlsMediaSource
import androidx.media3.session.MediaSession
import androidx.media3.session.MediaSessionService
import org.json.JSONObject
import java.net.HttpURLConnection
import java.net.URL
import java.security.SecureRandom
import java.util.Base64
import kotlin.concurrent.thread

private class PairingRequiredException(message:String):Exception(message)

class PlaybackService : MediaSessionService(), Player.Listener {
    companion object { const val ACTION_PLAY="xscan.PLAY"; const val ACTION_TOGGLE="xscan.TOGGLE"; const val ACTION_STOP="xscan.STOP" }
    private lateinit var player:ExoPlayer; private var session:MediaSession?=null
    private lateinit var wifiLock:WifiManager.WifiLock; private val handler=Handler(Looper.getMainLooper())
    private var failures=0; private var stoppedByUser=false
    private val prefs by lazy { getSharedPreferences("xscan",MODE_PRIVATE) }
    private val dataSource=DefaultHttpDataSource.Factory().setUserAgent("XScan-Android/${BuildConfig.VERSION_NAME}").setConnectTimeoutMs(7000).setReadTimeoutMs(15000)
    override fun onCreate(){super.onCreate();
        player=ExoPlayer.Builder(this).build().apply { setAudioAttributes(AudioAttributes.Builder().setUsage(C.USAGE_MEDIA).setContentType(C.AUDIO_CONTENT_TYPE_SPEECH).build(),true); setWakeMode(C.WAKE_MODE_NETWORK); addListener(this@PlaybackService) }
        session=MediaSession.Builder(this,player).build(); wifiLock=(applicationContext.getSystemService(Context.WIFI_SERVICE) as WifiManager).createWifiLock(WifiManager.WIFI_MODE_FULL_HIGH_PERF,"XScan:live").apply{setReferenceCounted(false)}
        registerReceiver(NoisyReceiver(),android.content.IntentFilter(AudioManager.ACTION_AUDIO_BECOMING_NOISY),RECEIVER_NOT_EXPORTED)
    }
    override fun onGetSession(controllerInfo:MediaSession.ControllerInfo)=session
    override fun onStartCommand(intent:Intent?,flags:Int,startId:Int):Int { when(intent?.action){ACTION_PLAY->{stoppedByUser=false; startLive()};ACTION_TOGGLE->if(player.isPlaying){player.pause();setStatus("paused","Live audio paused")} else if(player.mediaItemCount>0){player.play();setStatus("connecting","Resuming live audio")} else startLive();ACTION_STOP->{stoppedByUser=true;player.stop();setStatus("stopped","Live audio stopped");stopSelf()}}; return START_STICKY }
    private fun setStatus(state:String,message:String) { prefs.edit().putString("playback_status",JSONObject().put("state",state).put("message",message).put("route","public_https").toString()).apply() }
    private fun startLive(){ if(stoppedByUser)return;setStatus("connecting","Connecting securely to live scanner audio");if(!wifiLock.isHeld)wifiLock.acquire(); issueToken { token -> handler.post { dataSource.setDefaultRequestProperties(mapOf("Authorization" to "Bearer $token")); val base=publicUrl(); val path=prefs.getString("hls_path","/api/v1/stream/hls/scanner/index.m3u8")!!; player.setMediaSource(HlsMediaSource.Factory(dataSource).createMediaSource(MediaItem.fromUri(base.trimEnd('/')+path))); player.prepare(); player.play(); scheduleRefresh() } } }
    private fun issueToken(done:(String)->Unit)=thread(name="xscan-token") { try {
        val id=prefs.getString("device_id",null)?:throw PairingRequiredException("Pair this Android phone, then tap Listen Live again")
        val ts=System.currentTimeMillis()/1000; val bytes=ByteArray(24).also{SecureRandom().nextBytes(it)}; val nonce=Base64.getUrlEncoder().withoutPadding().encodeToString(bytes)
        val message="POST\n/api/v1/mobile/token\n$ts\n$nonce".toByteArray(); val body=JSONObject().put("device_id",id).put("timestamp",ts).put("nonce",nonce).put("signature",DeviceIdentity.sign(this,message)).toString()
        val c=URL(publicUrl().trimEnd('/')+"/api/v1/mobile/token").openConnection() as HttpURLConnection
        try {
            c.connectTimeout=7000;c.readTimeout=10000;c.requestMethod="POST";c.setRequestProperty("Content-Type","application/json");c.doOutput=true;c.outputStream.use{it.write(body.toByteArray())}
            val code=c.responseCode
            if(code in 200..299){done(JSONObject(c.inputStream.bufferedReader().use{it.readText()}).getString("access_token"));return@thread}
            val detail=runCatching{JSONObject(c.errorStream?.bufferedReader()?.use{it.readText()}.orEmpty()).optString("detail")}.getOrDefault("")
            if(code==401 && (detail.contains("not registered",true)||detail.contains("signature is invalid",true))){DeviceIdentity.clearRegistration(this);throw PairingRequiredException("Phone pairing expired. Tap Listen Live again to repair it")}
            throw Exception(if(detail.isBlank())"XScan authorization failed ($code)" else "XScan authorization failed: $detail ($code)")
        } finally { c.disconnect() }
    }catch(e:Exception){handler.post{if(e is PairingRequiredException)setStatus("pair_required",e.message?:"Pair this Android phone") else onConnectionFailure(e.message?:"Live audio connection failed")}} }
    private fun scheduleRefresh(){handler.removeCallbacksAndMessages("token");handler.postAtTime({if(!stoppedByUser)startLive()},"token",android.os.SystemClock.uptimeMillis()+240000)}
    private fun onConnectionFailure(message:String="Live audio connection failed"){if(stoppedByUser)return;failures++;setStatus("error",message);val delay=minOf(8000L,500L shl minOf(maxOf(0,failures-1),4));handler.postDelayed({startLive()},delay)}
    override fun onPlayerError(error:androidx.media3.common.PlaybackException)=onConnectionFailure(error.message?:"Live stream playback failed")
    override fun onIsPlayingChanged(isPlaying:Boolean){if(isPlaying){failures=0;setStatus("playing","Listening securely to ${Uri.parse(publicUrl()).host ?: "XScan"}")}else if(!stoppedByUser && player.playbackState==Player.STATE_READY)setStatus("paused","Live audio paused");if(!isPlaying && wifiLock.isHeld && stoppedByUser)wifiLock.release()}
    private fun publicUrl()=prefs.getString("public_url",BuildConfig.DEFAULT_PUBLIC_URL)!!
    override fun onTaskRemoved(rootIntent:Intent?){/* MediaSessionService keeps user-started playback alive. */}
    override fun onDestroy(){handler.removeCallbacksAndMessages(null);session?.release();player.release();if(wifiLock.isHeld)wifiLock.release();super.onDestroy()}
    inner class NoisyReceiver:android.content.BroadcastReceiver(){override fun onReceive(context:Context,intent:Intent){if(intent.action==AudioManager.ACTION_AUDIO_BECOMING_NOISY)player.pause()}}
}
