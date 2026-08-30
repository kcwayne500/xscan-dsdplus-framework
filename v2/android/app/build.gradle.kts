plugins {
    id("com.android.application")
}

val xscanPublicUrl = providers.gradleProperty("xscanPublicUrl")
    .orElse(providers.environmentVariable("XSCAN_PUBLIC_URL"))
    .orElse("https://scanner.example.com")
    .get()

android {
    namespace = "com.xscan.radio"
    compileSdk = 36

    defaultConfig {
        applicationId = "com.xscan.radio"
        minSdk = 26
        targetSdk = 36
        versionCode = 5
        versionName = "1.0.4"
        testInstrumentationRunner = "androidx.test.runner.AndroidJUnitRunner"
        buildConfigField("String", "DEFAULT_PUBLIC_URL", "\"${xscanPublicUrl.replace("\"", "\\\"")}\"")
    }

    signingConfigs {
        create("release") {
            val storePath = System.getenv("XSCAN_ANDROID_KEYSTORE")
            if (!storePath.isNullOrBlank()) {
                storeFile = file(storePath)
                storePassword = System.getenv("XSCAN_ANDROID_STORE_PASSWORD")
                keyAlias = System.getenv("XSCAN_ANDROID_KEY_ALIAS") ?: "xscan-release"
                keyPassword = System.getenv("XSCAN_ANDROID_KEY_PASSWORD") ?: storePassword
            }
        }
    }
    buildTypes {
        release {
            isMinifyEnabled = true
            isShrinkResources = true
            proguardFiles(getDefaultProguardFile("proguard-android-optimize.txt"), "proguard-rules.pro")
            signingConfig = signingConfigs.getByName("release")
        }
    }
    compileOptions { sourceCompatibility = JavaVersion.VERSION_17; targetCompatibility = JavaVersion.VERSION_17 }
    buildFeatures { buildConfig = true }
}

kotlin { compilerOptions { jvmTarget.set(org.jetbrains.kotlin.gradle.dsl.JvmTarget.JVM_17) } }

dependencies {
    implementation("androidx.activity:activity-ktx:1.12.4")
    implementation("androidx.appcompat:appcompat:1.7.1")
    implementation("androidx.core:core-ktx:1.17.0")
    implementation("androidx.media3:media3-exoplayer:1.11.0")
    implementation("androidx.media3:media3-exoplayer-hls:1.11.0")
    implementation("androidx.media3:media3-session:1.11.0")
    implementation("androidx.media3:media3-ui:1.11.0")
    implementation("org.bouncycastle:bcprov-jdk18on:1.83")
    testImplementation("junit:junit:4.13.2")
}
