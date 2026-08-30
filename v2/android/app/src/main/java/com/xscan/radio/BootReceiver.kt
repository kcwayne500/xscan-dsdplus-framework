package com.xscan.radio

import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.PendingIntent
import android.content.BroadcastReceiver
import android.content.Context
import android.content.Intent
import androidx.core.app.NotificationCompat

class BootReceiver:BroadcastReceiver(){
    override fun onReceive(context:Context,intent:Intent){
        if(intent.action!=Intent.ACTION_BOOT_COMPLETED)return
        val prefs=context.getSharedPreferences("xscan",Context.MODE_PRIVATE);if(prefs.getString("device_id",null)==null)return
        val manager=context.getSystemService(Context.NOTIFICATION_SERVICE) as NotificationManager
        manager.createNotificationChannel(NotificationChannel("xscan_resume","XScan resume",NotificationManager.IMPORTANCE_LOW))
        val play=PendingIntent.getService(context,22,Intent(context,PlaybackService::class.java).setAction(PlaybackService.ACTION_PLAY),PendingIntent.FLAG_UPDATE_CURRENT or PendingIntent.FLAG_IMMUTABLE)
        manager.notify(22,NotificationCompat.Builder(context,"xscan_resume").setSmallIcon(com.xscan.radio.R.drawable.ic_radio).setContentTitle("XScan is ready").setContentText("Tap Resume to listen live").addAction(0,"Resume",play).setAutoCancel(true).build())
    }
}
