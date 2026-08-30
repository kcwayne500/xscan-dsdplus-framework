package com.xscan.radio

import android.content.Context
import android.security.keystore.KeyGenParameterSpec
import android.security.keystore.KeyProperties
import android.util.Base64
import org.bouncycastle.jce.provider.BouncyCastleProvider
import java.security.KeyFactory
import java.security.KeyPairGenerator
import java.security.KeyStore
import java.security.Signature
import java.security.spec.PKCS8EncodedKeySpec
import javax.crypto.Cipher
import javax.crypto.KeyGenerator
import javax.crypto.SecretKey
import javax.crypto.spec.GCMParameterSpec

object DeviceIdentity {
    private const val WRAP_ALIAS = "xscan-mobile-key-wrap"
    private const val REGISTERED_PUBLIC_KEY = "registered_public_key"
    private val provider = BouncyCastleProvider()
    private fun wrappingKey(): SecretKey {
        val store=KeyStore.getInstance("AndroidKeyStore").apply{load(null)}
        if(!store.containsAlias(WRAP_ALIAS)) KeyGenerator.getInstance(KeyProperties.KEY_ALGORITHM_AES,"AndroidKeyStore").run { init(KeyGenParameterSpec.Builder(WRAP_ALIAS,KeyProperties.PURPOSE_ENCRYPT or KeyProperties.PURPOSE_DECRYPT).setBlockModes(KeyProperties.BLOCK_MODE_GCM).setEncryptionPaddings(KeyProperties.ENCRYPTION_PADDING_NONE).build()); generateKey() }
        return store.getKey(WRAP_ALIAS,null) as SecretKey
    }
    private fun clearStoredIdentity(context: Context) {
        context.getSharedPreferences("xscan_identity",Context.MODE_PRIVATE).edit().clear().commit()
        clearRegistration(context)
        KeyStore.getInstance("AndroidKeyStore").apply { load(null); if(containsAlias(WRAP_ALIAS))deleteEntry(WRAP_ALIAS) }
    }
    private fun readOrCreatePair(context: Context): java.security.KeyPair {
        val prefs=context.getSharedPreferences("xscan_identity",Context.MODE_PRIVATE)
        var pub=prefs.getString("public",null);var encrypted=prefs.getString("private",null);var iv=prefs.getString("iv",null)
        if(pub==null||encrypted==null||iv==null){
            val generated=KeyPairGenerator.getInstance("Ed25519",provider).generateKeyPair(); val cipher=Cipher.getInstance("AES/GCM/NoPadding").apply{init(Cipher.ENCRYPT_MODE,wrappingKey())}
            pub=Base64.encodeToString(generated.public.encoded,Base64.NO_WRAP);encrypted=Base64.encodeToString(cipher.doFinal(generated.private.encoded),Base64.NO_WRAP);iv=Base64.encodeToString(cipher.iv,Base64.NO_WRAP)
            prefs.edit().putString("public",pub).putString("private",encrypted).putString("iv",iv).apply()
        }
        val cipher=Cipher.getInstance("AES/GCM/NoPadding").apply{init(Cipher.DECRYPT_MODE,wrappingKey(),GCMParameterSpec(128,Base64.decode(iv,Base64.NO_WRAP)))}
        val privateKey=KeyFactory.getInstance("Ed25519",provider).generatePrivate(PKCS8EncodedKeySpec(cipher.doFinal(Base64.decode(encrypted,Base64.NO_WRAP))))
        val publicKey=KeyFactory.getInstance("Ed25519",provider).generatePublic(java.security.spec.X509EncodedKeySpec(Base64.decode(pub,Base64.NO_WRAP)))
        return java.security.KeyPair(publicKey,privateKey)
    }
    @Synchronized private fun pair(context: Context): java.security.KeyPair {
        return try { readOrCreatePair(context) } catch(_:Exception) {
            clearStoredIdentity(context)
            readOrCreatePair(context)
        }
    }
    fun publicKey(context: Context): String = Base64.encodeToString(pair(context).public.encoded.takeLast(32).toByteArray(), Base64.NO_WRAP)
    fun sign(context:Context,message: ByteArray): String = Base64.encodeToString(Signature.getInstance("Ed25519",provider).run { initSign(pair(context).private); update(message); sign() }, Base64.NO_WRAP)
    fun isRegistered(context: Context): Boolean {
        val prefs=context.getSharedPreferences("xscan",Context.MODE_PRIVATE)
        if(!prefs.contains("device_id"))return false
        val registered=prefs.getString(REGISTERED_PUBLIC_KEY,null)?:return false
        return registered==publicKey(context) && prefs.contains("device_id")
    }
    fun clearRegistration(context: Context) {
        context.getSharedPreferences("xscan",Context.MODE_PRIVATE).edit().remove("device_id").remove(REGISTERED_PUBLIC_KEY).commit()
    }
    fun saveRegistration(context: Context, id: String, publicUrl: String?, hls: String?) {
        val currentPublicKey=publicKey(context)
        context.getSharedPreferences("xscan", Context.MODE_PRIVATE).edit().putString("device_id",id).putString(REGISTERED_PUBLIC_KEY,currentPublicKey).apply {
            if (!publicUrl.isNullOrBlank()) putString("public_url",publicUrl)
            if (!hls.isNullOrBlank()) putString("hls_path",hls)
        }.apply()
    }
}
