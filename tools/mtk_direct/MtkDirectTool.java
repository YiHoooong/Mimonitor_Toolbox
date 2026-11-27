import android.content.BroadcastReceiver;
import android.content.ContentResolver;
import android.content.Context;
import android.content.Intent;
import android.content.IntentFilter;
import android.database.ContentObserver;
import android.net.Uri;
import android.os.Build;
import android.os.FileObserver;
import android.os.Handler;
import android.os.HandlerThread;
import android.os.Process;
import android.provider.Settings;
import android.util.Log;
import java.io.BufferedReader;
import java.io.BufferedWriter;
import java.io.File;
import java.io.FileOutputStream;
import java.io.InputStreamReader;
import java.io.OutputStreamWriter;
import java.lang.reflect.Constructor;
import java.lang.reflect.Method;
import java.net.BindException;
import java.net.InetAddress;
import java.net.InetSocketAddress;
import java.net.ServerSocket;
import java.net.Socket;
import java.nio.charset.StandardCharsets;
import java.util.ArrayList;
import java.util.Collections;
import java.util.HashMap;
import java.util.LinkedHashMap;
import java.util.LinkedHashSet;
import java.util.List;
import java.util.Map;
import java.util.Set;
import java.util.concurrent.TimeUnit;

public class MtkDirectTool {
    private static final String TAG = "MtkDirectTool";
    private static final String VERSION = "v2.2";
    private static final int DEFAULT_DAEMON_PORT = 27891;
    private static final String RESULT_FILE = "/data/data/mitv.service/cache/.jni_result";
    private static final String BATCH_RESULT_FILE =
            "/sdcard/Download/Mimonitor_Toolbox/.mtk_batch_result.txt";
    private static final String SOURCE_RESULT_FILE =
            "/sdcard/Download/Mimonitor_Toolbox/.mtk_source_result.txt";
    private static final String TV_CLASSPATH =
            "/system/framework/mitvmiddlewareimpl.jar:/system_ext/priv-app/TvServices/TvServices.apk";
    private static final String NATIVE_LIB_PATH = "/vendor/lib64:/system/lib64:/system_ext/lib64";
    private static final String BSP_IFACE = "vendor.mi.hardware.bspmserver@2.0::IBspMServer";
    private static final String CONFIG_IFACE = "vendor.mi.hardware.bspmserver@2.0::IVendorConfig";
    private static final String PIC_MODE_CHANGED_ACTION = "com.xiaomi.mitv.action.PIC_MODE_CHANGED";

    // Cached reflection handles (shared by CLI and resident daemon)
    private static ClassLoader sTvClassLoader;
    private static Method sGetConfigValueNative;
    private static Method sSetConfigValueNative;
    private static Method sGetMinMaxNative;

    private static Class<?> sDisplayClass;
    private static Object sDisplayInstance;
    private static Object sUserColorTempEnum;
    private static Constructor<?> sColorTempDataCtor;
    private static Method sSetWbGainOffsetExMethod;

    private static Class<?> sMiTvProviderClass;
    private static Object sMiTvProvider;
    private static Method sSetHdrAttrMethod;

    private static Class<?> sIAudioServiceClass;
    private static Object sAudioService;
    private static Method sAudioGetParamsMethod;
    private static Method sAudioSetParamsMethod;
    private static Method sAudioGetOutputDeviceNameMethod;

    private static Class<?> sHwBinderClass;
    private static Class<?> sHwParcelClass;
    private static Class<?> sIHwBinderClass;
    private static Object sLedConfigBinder;
    private static Method sHwParcelWriteIfaceMethod;
    private static Method sHwParcelWriteInt32Method;
    private static Method sHwParcelVerifySuccessMethod;
    private static Method sHwParcelReleaseTempMethod;
    private static Method sHwParcelReadInt32Method;
    private static Method sHwParcelReadStrongBinderMethod;
    private static Method sHwParcelReleaseMethod;
    private static Method sHwBinderGetServiceMethod;
    private static Method sIHwBinderTransactMethod;

    private static Method sSmGetServiceMethod;
    private static Method sIBinderTransactMethod;
    private static Method sParcelObtainMethod;
    private static Method sParcelWriteIfaceMethod;
    private static Method sParcelWriteIntMethod;
    private static Method sParcelReadExceptionMethod;
    private static Method sParcelReadIntMethod;
    private static Method sParcelRecycleMethod;

    private static Context sContext;
    private static ContentResolver sResolver;
    private static boolean sResolverGetBroken = false;
    private static boolean sResolverPutBroken = false;
    private static boolean sSendBroadcastBroken = false;
    private static String sCachedModel;

    private static void log(String msg) {
        System.out.println(msg);
        Log.d(TAG, msg);
    }

    private static void writeFile(String path, String value) {
        File file = new File(path);
        File parent = file.getParentFile();
        if (parent != null && !parent.exists()) {
            parent.mkdirs();
        }
        File tmp = new File(parent, file.getName() + ".tmp." + Process.myPid());
        String content = value == null ? "" : value;
        try {
            try (BufferedWriter bw = new BufferedWriter(
                    new OutputStreamWriter(new FileOutputStream(tmp), StandardCharsets.UTF_8))) {
                bw.write(content);
                bw.flush();
            }
            if (!tmp.renameTo(file)) {
                if (file.exists()) {
                    file.delete();
                }
                if (!tmp.renameTo(file)) {
                    try (BufferedWriter bw = new BufferedWriter(
                            new OutputStreamWriter(new FileOutputStream(file), StandardCharsets.UTF_8))) {
                        bw.write(content);
                        bw.flush();
                    }
                }
            }
        } catch (Exception e) {
            Log.e(TAG, "Failed to write result file: " + e.getMessage());
        } finally {
            if (tmp.exists()) {
                tmp.delete();
            }
        }
    }

    private static void writeResult(String value) {
        writeFile(RESULT_FILE, value);
    }

    private static void writeBatchResult(String value) {
        writeFile(BATCH_RESULT_FILE, value);
    }

    private static String sanitizeSingleLine(String text) {
        if (text == null) {
            return "";
        }
        return text.replace("\r", "").replace("\n", " ");
    }

    private static int clamp(int v, int lo, int hi) {
        return Math.max(lo, Math.min(hi, v));
    }

    private static int parseIntOr(String s, int def) {
        try {
            return Integer.parseInt(s);
        } catch (Exception e) {
            return def;
        }
    }

    private static synchronized ClassLoader ensureTvClassLoader() throws Exception {
        if (sTvClassLoader == null) {
            sTvClassLoader = (ClassLoader) Class.forName("dalvik.system.PathClassLoader")
                    .getConstructor(String.class, String.class, ClassLoader.class)
                    .newInstance(TV_CLASSPATH, NATIVE_LIB_PATH, ClassLoader.getSystemClassLoader());
        }
        return sTvClassLoader;
    }

    private static synchronized void ensureTvNative() throws Exception {
        if (sGetConfigValueNative != null && sSetConfigValueNative != null && sGetMinMaxNative != null) {
            return;
        }
        ClassLoader classLoader = ensureTvClassLoader();
        Class<?> tvNativeClass = classLoader.loadClass("com.mediatek.twoworlds.tv.TVNative");
        sGetConfigValueNative = tvNativeClass.getDeclaredMethod(
                "getConfigValue_native", int.class, String.class);
        sGetConfigValueNative.setAccessible(true);
        sSetConfigValueNative = tvNativeClass.getDeclaredMethod(
                "setConfigValue_native", int.class, String.class, int.class, int.class);
        sSetConfigValueNative.setAccessible(true);
        sGetMinMaxNative = tvNativeClass.getDeclaredMethod(
                "getMinMaxConfigValue_native", int.class, String.class);
        sGetMinMaxNative.setAccessible(true);
    }

    @SuppressWarnings({"unchecked", "rawtypes"})
    private static synchronized void ensureDisplayHandles() throws Exception {
        if (sDisplayInstance != null && sSetWbGainOffsetExMethod != null) {
            return;
        }
        ClassLoader classLoader = ensureTvClassLoader();
        sDisplayClass = classLoader.loadClass("com.mediatek.twoworlds.factory.MtkTvFApiDisplay");
        sDisplayInstance = sDisplayClass.getMethod("getInstance").invoke(null);

        Class<?> enumClass = classLoader.loadClass(
                "com.mediatek.twoworlds.factory.common.MtkTvFApiDisplayTypes$EnumColorTemperature");
        sUserColorTempEnum = Enum.valueOf(
                (Class<Enum>) enumClass.asSubclass(Enum.class),
                "E_MTK_FAPI_COLOR_TEMP_USER");

        Class<?> dataClass = classLoader.loadClass(
                "com.mediatek.twoworlds.factory.common.MtkTvFApiDisplayTypes$ColorTempData");
        sColorTempDataCtor = dataClass.getConstructor(
                int.class, int.class, int.class, int.class, int.class, int.class);

        sSetWbGainOffsetExMethod = sDisplayClass.getMethod(
                "setWbGainOffsetEx", enumClass, dataClass, int.class);
    }

    private static synchronized void ensureContext() {
        if (sContext != null && sResolver != null) {
            return;
        }
        try {
            Class<?> looperClass = Class.forName("android.os.Looper");
            Object mainLooper = looperClass.getMethod("getMainLooper").invoke(null);
            if (mainLooper == null) {
                looperClass.getMethod("prepareMainLooper").invoke(null);
            }
            Class<?> atClass = Class.forName("android.app.ActivityThread");
            Object at = atClass.getMethod("currentActivityThread").invoke(null);
            if (at == null) {
                at = atClass.getMethod("systemMain").invoke(null);
            }
            if (at != null) {
                Object ctx = atClass.getMethod("getSystemContext").invoke(at);
                if (ctx instanceof Context) {
                    Context baseCtx = (Context) ctx;
                    sContext = baseCtx;
                    sResolver = baseCtx.getContentResolver();
                    sResolverGetBroken = false;
                    sResolverPutBroken = false;
                    boolean probeOk = false;
                    try {
                        Settings.Global.getString(sResolver, "picture_mode");
                        probeOk = true;
                    } catch (Throwable ignoredProbe) {
                    }
                    if (!probeOk) {
                        String[] candidatePkgs = {
                                "mitv.service",
                                "com.xiaomi.mitv.settings",
                                "com.android.providers.settings",
                                "com.android.shell"
                        };
                        for (String pkg : candidatePkgs) {
                            try {
                                Context pkgCtx = baseCtx.createPackageContext(
                                        pkg, Context.CONTEXT_IGNORE_SECURITY);
                                ContentResolver pkgResolver = pkgCtx.getContentResolver();
                                Settings.Global.getString(pkgResolver, "picture_mode");
                                sContext = pkgCtx;
                                sResolver = pkgResolver;
                                probeOk = true;
                                break;
                            } catch (Throwable ignoredPkg) {
                            }
                        }
                    }
                    if (!probeOk) {
                        sResolverGetBroken = true;
                        sResolverPutBroken = true;
                    }
                }
            }
        } catch (Throwable t) {
            Log.w(TAG, "ensureContext failed: " + t.getMessage());
            sContext = null;
            sResolver = null;
        }
    }

    private static int getJniValue(String key) throws Exception {
        ensureTvNative();
        return (Integer) sGetConfigValueNative.invoke(null, -1, key);
    }

    private static int setJniValue(String key, int value, int isUpdate) throws Exception {
        ensureTvNative();
        return (Integer) sSetConfigValueNative.invoke(null, -1, key, value, isUpdate);
    }

    private static int rampJni(String key, int from, int target, int isUpdate, int stepMs,
            String doneFile, int stepSize) throws Exception {
        ensureTvNative();
        int clampedStepSize = Math.max(1, stepSize);
        int boundedStepMs = clamp(stepMs, 0, 1000);
        long sleepBudgetMs = 15000L;
        long sleptMs = 0L;
        if (doneFile != null && doneFile.length() > 0) {
            try {
                new File(doneFile).delete();
            } catch (Exception ignored) {
            }
        }
        int applied = from;
        int v = from;
        int step = target >= from ? clampedStepSize : -clampedStepSize;
        while (true) {
            sSetConfigValueNative.invoke(null, -1, key, v, isUpdate);
            applied = v;
            if (v == target) {
                break;
            }
            int next = v + step;
            if ((step > 0 && next > target) || (step < 0 && next < target)) {
                next = target;
            }
            v = next;
            if (boundedStepMs > 0 && sleptMs < sleepBudgetMs) {
                long sleepNow = Math.min((long) boundedStepMs, sleepBudgetMs - sleptMs);
                Thread.sleep(sleepNow);
                sleptMs += sleepNow;
            }
        }
        if (doneFile != null && doneFile.length() > 0) {
            writeFile(doneFile, "1");
        }
        return applied;
    }

    private static int setColorGains(int red, int green, int blue) throws Exception {
        ensureDisplayHandles();
        Object data = sColorTempDataCtor.newInstance(red, green, blue, 1024, 1024, 1024);
        return (Integer) sSetWbGainOffsetExMethod.invoke(sDisplayInstance, sUserColorTempEnum, data, 0);
    }

    private static Integer trySetHdrAttr(int value) {
        for (int attempt = 0; attempt < 2; attempt++) {
            try {
                if (sMiTvProvider == null || sSetHdrAttrMethod == null || sMiTvProviderClass == null) {
                    ClassLoader classLoader = ensureTvClassLoader();
                    sMiTvProviderClass = classLoader.loadClass(
                            "xiaomi.hardware.mitv.provider.V1_0.IMiTVProvider");
                    sMiTvProvider = sMiTvProviderClass.getMethod("getService", boolean.class)
                            .invoke(null, false);
                    sSetHdrAttrMethod = sMiTvProviderClass.getMethod("setHdrAttr", int.class, int.class);
                }
                if (sMiTvProvider == null) {
                    return null;
                }
                return (Integer) sSetHdrAttrMethod.invoke(sMiTvProvider, 4, value);
            } catch (Exception e) {
                sMiTvProvider = null;
                if (attempt == 1) {
                    log("WARN: setHdrAttr failed: " + e.getMessage());
                }
            }
        }
        return null;
    }

    private static int[] setHdrToneMapping(int value, int isUpdate) throws Exception {
        int configRet = setJniValue("g_video__vid_hdr_tone_mapping_mode", value, isUpdate);
        Integer hdrAttrRet = trySetHdrAttr(value);
        return new int[]{configRet, hdrAttrRet == null ? Integer.MIN_VALUE : hdrAttrRet};
    }

    private static void batchGet(String[] keys, int startIndex) throws Exception {
        ensureTvNative();
        StringBuilder sb = new StringBuilder();
        long startMs = System.currentTimeMillis();
        int count = 0;
        for (int i = startIndex; i < keys.length; i++) {
            String k = keys[i];
            if (k == null || k.length() == 0) {
                continue;
            }
            count++;
            try {
                int val = (Integer) sGetConfigValueNative.invoke(null, -1, k);
                sb.append(k).append("=").append(val).append("\n");
            } catch (Throwable e) {
                sb.append(k).append("=ERROR:").append(e.getClass().getSimpleName()).append("\n");
            }
        }
        sb.append("__elapsed_ms=").append(System.currentTimeMillis() - startMs).append("\n");
        writeBatchResult(sb.toString());
        log("RESULT: BATCH_GET keys=" + count);
    }

    private static synchronized void ensureTvServiceReflection() throws Exception {
        if (sSmGetServiceMethod != null && sIBinderTransactMethod != null && sParcelRecycleMethod != null) {
            return;
        }
        Class<?> smClass = Class.forName("android.os.ServiceManager");
        sSmGetServiceMethod = smClass.getMethod("getService", String.class);
        Class<?> iBinderClass = Class.forName("android.os.IBinder");
        Class<?> parcelClass = Class.forName("android.os.Parcel");
        sParcelObtainMethod = parcelClass.getMethod("obtain");
        sParcelWriteIfaceMethod = parcelClass.getMethod("writeInterfaceToken", String.class);
        sParcelWriteIntMethod = parcelClass.getMethod("writeInt", int.class);
        sIBinderTransactMethod = iBinderClass.getMethod(
                "transact", int.class, parcelClass, parcelClass, int.class);
        sParcelReadExceptionMethod = parcelClass.getMethod("readException");
        sParcelReadIntMethod = parcelClass.getMethod("readInt");
        sParcelRecycleMethod = parcelClass.getMethod("recycle");
    }

    private static void recycleParcelQuietly(Object parcel) {
        if (parcel != null && sParcelRecycleMethod != null) {
            try {
                sParcelRecycleMethod.invoke(parcel);
            } catch (Throwable ignored) {
            }
        }
    }

    private static int transactTvServiceParcel(int code, Integer writeArg, boolean readInt) {
        Object data = null;
        Object reply = null;
        try {
            ensureTvServiceReflection();
            Object tvBinder = sSmGetServiceMethod.invoke(null, "TvService");
            if (tvBinder == null) {
                log("WARN: TvService binder is null for tx " + code);
                return -1;
            }
            data = sParcelObtainMethod.invoke(null);
            reply = sParcelObtainMethod.invoke(null);
            sParcelWriteIfaceMethod.invoke(data, "mitv.internal.ITvService");
            if (writeArg != null) {
                sParcelWriteIntMethod.invoke(data, writeArg.intValue());
            }
            boolean ok = (Boolean) sIBinderTransactMethod.invoke(tvBinder, code, data, reply, 0);
            sParcelReadExceptionMethod.invoke(reply);
            if (readInt) {
                return (Integer) sParcelReadIntMethod.invoke(reply);
            }
            return ok ? 1 : 0;
        } catch (Throwable t) {
            log("WARN: TvService tx " + code + " failed: " + t.getMessage());
            return -1;
        } finally {
            recycleParcelQuietly(reply);
            recycleParcelQuietly(data);
        }
    }

    private static boolean transactTvService(int code, int arg) {
        return transactTvServiceParcel(code, Integer.valueOf(arg), false) == 1;
    }

    private static int queryTvServiceInt(int code) {
        return transactTvServiceParcel(code, null, true);
    }

    private static boolean disconnectSpdif(Class<?> iAudioServiceClass, Object audioService) {
        try {
            Class<?> adaClass = Class.forName("android.media.AudioDeviceAttributes");
            Object ada = null;
            try {
                // Android 14 native-type constructor used by AudioManager.setWiredDeviceConnectionState
                Constructor<?> ctorNative = adaClass.getConstructor(int.class, String.class, String.class);
                ada = ctorNative.newInstance(524288, "", "Spdif");
            } catch (NoSuchMethodException ignored) {
                // Fallback: (role=2, deviceType, address) where deviceType is AudioDeviceInfo.TYPE_*
                Constructor<?> ctorRole = adaClass.getConstructor(int.class, int.class, String.class);
                int spdifPublicType = 524288;
                try {
                    Class<?> infoClass = Class.forName("android.media.AudioDeviceInfo");
                    spdifPublicType = (Integer) infoClass
                            .getMethod("convertInternalDeviceToDeviceType", int.class)
                            .invoke(null, 524288);
                } catch (Throwable ignoredInfo) {
                }
                ada = ctorRole.newInstance(2, spdifPublicType, "");
            }
            iAudioServiceClass
                    .getMethod("setWiredDeviceConnectionState", adaClass, int.class, String.class)
                    .invoke(audioService, ada, 0, "mitv.service");
            return true;
        } catch (NoSuchMethodException | ClassNotFoundException e) {
            try {
                iAudioServiceClass
                        .getMethod("setWiredDeviceConnectionState", int.class, int.class, String.class, String.class)
                        .invoke(audioService, 524288, 0, "", "Spdif");
                return true;
            } catch (Throwable t) {
                log("WARN: disconnectSpdif fallback failed: " + t.getMessage());
                return false;
            }
        } catch (Throwable t) {
            log("WARN: disconnectSpdif failed: " + t.getMessage());
            return false;
        }
    }

    private static synchronized void ensureAudioHandles(boolean forceRefresh) throws Exception {
        if (forceRefresh) {
            sAudioService = null;
        }
        if (sAudioGetParamsMethod == null) {
            Class<?> audioSystemClass = Class.forName("android.media.AudioSystem");
            sAudioGetParamsMethod = audioSystemClass.getMethod("getParameters", String.class);
            sAudioSetParamsMethod = audioSystemClass.getMethod("setParameters", String.class);
            sAudioGetOutputDeviceNameMethod = audioSystemClass.getMethod("getOutputDeviceName", int.class);
            sIAudioServiceClass = Class.forName("android.media.IAudioService");
        }
        if (sAudioService == null) {
            Class<?> smClass = Class.forName("android.os.ServiceManager");
            Object audioBinder = smClass.getMethod("getService", String.class).invoke(null, "audio");
            if (audioBinder == null) {
                throw new IllegalStateException("ServiceManager.getService(\"audio\") returned null");
            }
            Class<?> iBinderClass = Class.forName("android.os.IBinder");
            Class<?> stubClass = Class.forName("android.media.IAudioService$Stub");
            sAudioService = stubClass.getMethod("asInterface", iBinderClass).invoke(null, audioBinder);
            if (sAudioService == null) {
                throw new IllegalStateException("IAudioService$Stub.asInterface returned null");
            }
        }
    }

    private static void setSoundOutput(int deviceId) throws Exception {
        int androidDev;
        int forceConfig;
        boolean hdmiArc;
        switch (deviceId) {
            case 0:
                androidDev = 2;
                forceConfig = 1;
                hdmiArc = false;
                break;
            case 3:
            case 8:
                androidDev = 262144;
                forceConfig = 16;
                hdmiArc = true;
                break;
            case 5:
                androidDev = 4;
                forceConfig = 2;
                hdmiArc = false;
                break;
            default:
                throw new IllegalArgumentException("Unsupported deviceId " + deviceId);
        }

        try {
            ensureAudioHandles(false);
        } catch (Exception e) {
            ensureAudioHandles(true);
        }

        String spdifParam = (String) sAudioGetParamsMethod.invoke(null, "xmfeaspdifdet");
        boolean spdifSupported = "xmfeaspdifdet=support".equals(spdifParam);

        sAudioSetParamsMethod.invoke(null, "xiaomi_switch_device=true");
        int forceRet;
        int hdmiRet;
        boolean spdifDisconnected = false;
        int paramRet;
        String devName;
        try {
            try {
                forceRet = (Integer) sIAudioServiceClass
                        .getMethod("xmSetForceUse", int.class, int.class)
                        .invoke(sAudioService, 1, forceConfig);
            } catch (Exception firstErr) {
                ensureAudioHandles(true);
                forceRet = (Integer) sIAudioServiceClass
                        .getMethod("xmSetForceUse", int.class, int.class)
                        .invoke(sAudioService, 1, forceConfig);
            }
            hdmiRet = (Integer) sIAudioServiceClass
                    .getMethod("setHdmiSystemAudioSupported", boolean.class)
                    .invoke(sAudioService, hdmiArc);
            if (!spdifSupported) {
                spdifDisconnected = disconnectSpdif(sIAudioServiceClass, sAudioService);
            }
            devName = (String) sAudioGetOutputDeviceNameMethod.invoke(null, androidDev);
            paramRet = (Integer) sAudioSetParamsMethod.invoke(null, "switch_output_device=" + devName);
        } finally {
            try {
                sAudioSetParamsMethod.invoke(null, "xiaomi_switch_device=false");
            } catch (Throwable ignored) {
            }
        }

        boolean tvSaved = transactTvService(32, deviceId);
        boolean tvBroadcast = transactTvService(33, deviceId);

        log("RESULT: SET_SOUND_OUTPUT deviceId=" + deviceId
                + " androidDev=" + androidDev
                + " devName=" + devName
                + " forceRet=" + forceRet
                + " hdmiRet=" + hdmiRet
                + " paramRet=" + paramRet
                + " spdif=" + (spdifSupported ? "supported" : (spdifDisconnected ? "disconnected" : "disconnect_failed"))
                + " tvSaved=" + tvSaved
                + " tvBroadcast=" + tvBroadcast);
        writeResult("OK:" + deviceId);
    }

    private static String querySoundOutputSummary() throws Exception {
        try {
            ensureAudioHandles(false);
        } catch (Exception e) {
            ensureAudioHandles(true);
        }
        int forceUse;
        try {
            forceUse = (Integer) sIAudioServiceClass
                    .getMethod("xmGetForceUse", int.class)
                    .invoke(sAudioService, 1);
        } catch (Exception e) {
            ensureAudioHandles(true);
            forceUse = (Integer) sIAudioServiceClass
                    .getMethod("xmGetForceUse", int.class)
                    .invoke(sAudioService, 1);
        }
        boolean hdmiAudio = (Boolean) sIAudioServiceClass
                .getMethod("isHdmiSystemAudioSupported")
                .invoke(sAudioService);
        String spdifParam = (String) sAudioGetParamsMethod.invoke(null, "xmfeaspdifdet");
        int tvDevice = queryTvServiceInt(34);

        log("RESULT: GET_SOUND_OUTPUT forceUseMedia=" + forceUse
                + " hdmiSystemAudio=" + hdmiAudio
                + " tvDevice=" + tvDevice
                + " spdif=" + spdifParam);
        return forceUse + "," + hdmiAudio + "," + tvDevice;
    }

    private static int getMediaVolume() throws Exception {
        for (int attempt = 0; attempt < 2; attempt++) {
            try {
                ensureAudioHandles(attempt > 0);
                try {
                    Method m = sIAudioServiceClass.getMethod("getStreamVolumeForMute", int.class);
                    return (Integer) m.invoke(sAudioService, 3);
                } catch (NoSuchMethodException e) {
                    Method m = sIAudioServiceClass.getMethod("getStreamVolume", int.class);
                    return (Integer) m.invoke(sAudioService, 3);
                }
            } catch (Exception e) {
                sAudioService = null;
                if (attempt == 1) {
                    throw e;
                }
            }
        }
        throw new IllegalStateException("getMediaVolume unreachable");
    }

    private static boolean getMediaMuted() throws Exception {
        for (int attempt = 0; attempt < 2; attempt++) {
            try {
                ensureAudioHandles(attempt > 0);
                Method m = sIAudioServiceClass.getMethod("isStreamMute", int.class);
                return (Boolean) m.invoke(sAudioService, 3);
            } catch (Exception e) {
                sAudioService = null;
                if (attempt == 1) {
                    throw e;
                }
            }
        }
        throw new IllegalStateException("getMediaMuted unreachable");
    }

    private static String audioCallerPackage() {
        ensureContext();
        if (sContext != null) {
            try {
                String op = sContext.getOpPackageName();
                if (op != null && op.length() > 0) {
                    return op;
                }
            } catch (Throwable ignored) {
            }
        }
        return "mitv.service";
    }

    private static boolean setMediaVolume(int target, boolean unmuteFirst, boolean showUi) {
        int clamped = clamp(target, 0, 100);
        int flags = showUi ? 1 : 0;
        for (int attempt = 0; attempt < 2; attempt++) {
            try {
                ensureAudioHandles(attempt > 0);
                if (unmuteFirst) {
                    setMediaMuted(false, false);
                }
                Method setVol = sIAudioServiceClass.getMethod(
                        "setStreamVolume", int.class, int.class, int.class, String.class);
                String pkg = audioCallerPackage();
                try {
                    setVol.invoke(sAudioService, 3, clamped, flags, pkg);
                } catch (Exception e) {
                    setVol.invoke(sAudioService, 3, clamped, flags, "com.android.shell");
                }
                return getMediaVolume() == clamped;
            } catch (Throwable t) {
                sAudioService = null;
            }
        }
        return false;
    }

    private static boolean setMediaMuted(boolean muted, boolean showUi) {
        int flags = showUi ? 1 : 0;
        int direction = muted ? -100 : 100;
        for (int attempt = 0; attempt < 2; attempt++) {
            try {
                ensureAudioHandles(attempt > 0);
                Method adjVol = sIAudioServiceClass.getMethod(
                        "adjustStreamVolume", int.class, int.class, int.class, String.class);
                String pkg = audioCallerPackage();
                try {
                    adjVol.invoke(sAudioService, 3, direction, flags, pkg);
                } catch (Exception e) {
                    adjVol.invoke(sAudioService, 3, direction, flags, "com.android.shell");
                }
                return getMediaMuted() == muted;
            } catch (Throwable t) {
                sAudioService = null;
            }
        }
        return false;
    }

    private static synchronized void ensureLedReflection() throws Exception {
        if (sHwBinderClass != null && sIHwBinderTransactMethod != null && sHwBinderGetServiceMethod != null) {
            return;
        }
        sHwBinderClass = Class.forName("android.os.HwBinder");
        sHwParcelClass = Class.forName("android.os.HwParcel");
        sIHwBinderClass = Class.forName("android.os.IHwBinder");
        sHwBinderGetServiceMethod = sHwBinderClass.getMethod("getService", String.class, String.class);
        sHwParcelWriteIfaceMethod = sHwParcelClass.getMethod("writeInterfaceToken", String.class);
        sHwParcelWriteInt32Method = sHwParcelClass.getMethod("writeInt32", int.class);
        sHwParcelVerifySuccessMethod = sHwParcelClass.getMethod("verifySuccess");
        sHwParcelReleaseTempMethod = sHwParcelClass.getMethod("releaseTemporaryStorage");
        sHwParcelReadInt32Method = sHwParcelClass.getMethod("readInt32");
        sHwParcelReadStrongBinderMethod = sHwParcelClass.getMethod("readStrongBinder");
        sHwParcelReleaseMethod = sHwParcelClass.getMethod("release");
        sIHwBinderTransactMethod = sIHwBinderClass.getMethod(
                "transact", int.class, sHwParcelClass, sHwParcelClass, int.class);
    }

    private static void releaseHwParcelQuietly(Object parcel) {
        if (parcel != null && sHwParcelReleaseMethod != null) {
            try {
                sHwParcelReleaseMethod.invoke(parcel);
            } catch (Throwable ignored) {
            }
        }
    }

    private static Object getLedConfigBinder(boolean forceRefresh) throws Exception {
        ensureLedReflection();
        if (!forceRefresh && sLedConfigBinder != null) {
            return sLedConfigBinder;
        }
        sLedConfigBinder = null;
        Object bsp = sHwBinderGetServiceMethod.invoke(null, BSP_IFACE, "default");
        if (bsp == null) {
            throw new IllegalStateException("HwBinder.getService(" + BSP_IFACE + ") returned null");
        }
        Object req = sHwParcelClass.getConstructor().newInstance();
        Object reply = sHwParcelClass.getConstructor().newInstance();
        try {
            sHwParcelWriteIfaceMethod.invoke(req, BSP_IFACE);
            sIHwBinderTransactMethod.invoke(bsp, 6, req, reply, 0);
            sHwParcelVerifySuccessMethod.invoke(reply);
            sHwParcelReleaseTempMethod.invoke(req);
            int status = (Integer) sHwParcelReadInt32Method.invoke(reply);
            Object config = sHwParcelReadStrongBinderMethod.invoke(reply);
            if (status != 0 || config == null) {
                throw new RuntimeException("CreateConfigInterface failed status=" + status + " binder=" + config);
            }
            sLedConfigBinder = config;
            return config;
        } finally {
            releaseHwParcelQuietly(reply);
            releaseHwParcelQuietly(req);
        }
    }

    private static int setColorfulLedRaw(int mode, int color) throws Exception {
        Exception lastErr = null;
        for (int attempt = 0; attempt < 2; attempt++) {
            Object config = getLedConfigBinder(attempt > 0);
            Object req = sHwParcelClass.getConstructor().newInstance();
            Object reply = sHwParcelClass.getConstructor().newInstance();
            try {
                sHwParcelWriteIfaceMethod.invoke(req, CONFIG_IFACE);
                sHwParcelWriteInt32Method.invoke(req, mode);
                sHwParcelWriteInt32Method.invoke(req, color);
                sIHwBinderTransactMethod.invoke(config, 2, req, reply, 0);
                sHwParcelVerifySuccessMethod.invoke(reply);
                sHwParcelReleaseTempMethod.invoke(req);
                return (Integer) sHwParcelReadInt32Method.invoke(reply);
            } catch (Exception e) {
                sLedConfigBinder = null;
                lastErr = e;
            } finally {
                releaseHwParcelQuietly(reply);
                releaseHwParcelQuietly(req);
            }
        }
        throw lastErr;
    }

    private static int lightingColor(int illumination, int colorTemp) {
        illumination = clamp(illumination, 0, 14);
        colorTemp = clamp(colorTemp, 0, 2);
        if (colorTemp == 1) {
            return 13 + illumination;
        }
        if (colorTemp == 0) {
            return 28 + illumination;
        }
        return 43 + illumination;
    }

    private static int solidColor(int illumination, int colorValue) {
        illumination = clamp(illumination, 0, 14);
        colorValue = clamp(colorValue, 0, 4);
        int[] base = {58, 73, 103, 88, 118};
        return base[colorValue] + illumination;
    }

    private static int[] resolveLedModeAndColor(String[] args, int offset) {
        if (offset >= args.length) {
            throw new IllegalArgumentException("led requires action");
        }
        String cmd = args[offset];
        String arg1 = offset + 1 < args.length ? args[offset + 1] : null;
        String arg2 = offset + 2 < args.length ? args[offset + 2] : null;
        int mode;
        int color;
        if ("off".equals(cmd)) {
            mode = 0;
            color = 1;
        } else if ("ambient".equals(cmd)) {
            mode = 12;
            color = 1;
        } else if ("cycle".equals(cmd)) {
            mode = 3;
            color = 1;
        } else if ("lighting".equals(cmd)) {
            mode = 11;
            color = lightingColor(parseIntOr(arg1, 9), parseIntOr(arg2, 1));
        } else if ("solid".equals(cmd)) {
            mode = 1;
            color = solidColor(parseIntOr(arg1, 9), parseIntOr(arg2, 0));
        } else if ("raw".equals(cmd)) {
            mode = parseIntOr(arg1, 0);
            color = parseIntOr(arg2, 0);
        } else {
            throw new IllegalArgumentException("Unknown led command: " + cmd);
        }
        return new int[]{mode, color};
    }

    private static int runColorfulLed(String[] args, int offset) throws Exception {
        int[] pair = resolveLedModeAndColor(args, offset);
        return setColorfulLedRaw(pair[0], pair[1]);
    }

    private static List<String> execCommandAndReadLines(String... cmd) throws Exception {
        final java.lang.Process p = new ProcessBuilder(cmd).redirectErrorStream(true).start();
        final List<String> lines = new ArrayList<>();
        final Throwable[] readErr = new Throwable[1];
        Thread reader = new Thread(new Runnable() {
            @Override
            public void run() {
                try (BufferedReader br = new BufferedReader(
                        new InputStreamReader(p.getInputStream(), StandardCharsets.UTF_8))) {
                    String line;
                    while ((line = br.readLine()) != null) {
                        lines.add(line);
                    }
                } catch (Throwable t) {
                    readErr[0] = t;
                }
            }
        }, "mtk-cmd-reader");
        reader.setDaemon(true);
        try {
            try {
                p.getOutputStream().close();
            } catch (Throwable ignored) {
            }
            reader.start();
            boolean finished = p.waitFor(5, TimeUnit.SECONDS);
            if (!finished) {
                p.destroyForcibly();
                p.waitFor(1, TimeUnit.SECONDS);
                throw new IllegalStateException("Command timed out: " + String.join(" ", cmd));
            }
            reader.join(500);
            int exit = p.exitValue();
            if (exit != 0) {
                throw new IllegalStateException("Command failed (" + exit + "): " + String.join(" ", lines));
            }
            return lines;
        } finally {
            p.destroyForcibly();
        }
    }

    private static String execCommandAndReadFirstLine(String... cmd) throws Exception {
        List<String> lines = execCommandAndReadLines(cmd);
        StringBuilder out = new StringBuilder();
        for (String line : lines) {
            String trimmed = line.trim();
            if (trimmed.length() == 0) {
                continue;
            }
            if (out.length() > 0) {
                out.append(" ");
            }
            out.append(trimmed);
        }
        return out.toString();
    }

    private static void setInputSource(int sourceId) throws Exception {
        if (sourceId <= 0) {
            throw new IllegalArgumentException("Invalid sourceId: " + sourceId);
        }
        execCommandAndReadLines("am", "force-stop", "com.xiaomi.mitv.tvplayer");
        execCommandAndReadLines(
                "am",
                "start",
                "-a",
                "com.xiaomi.mitv.tvplayer.EXTSRC_PLAY",
                "-n",
                "com.xiaomi.mitv.tvplayer/.ExternalSourceActivity",
                "--ei",
                "input",
                String.valueOf(sourceId),
                "-f",
                "0x10000000"
        );
    }

    private static String getGlobalSettingViaCli(String key) throws Exception {
        try {
            return sanitizeSingleLine(execCommandAndReadFirstLine("cmd", "settings", "get", "global", key));
        } catch (Throwable t) {
            return sanitizeSingleLine(execCommandAndReadFirstLine("settings", "get", "global", key));
        }
    }

    private static Map<String, String> getGlobalSettingsBulk(List<String> keys) throws Exception {
        Map<String, String> result = new HashMap<>();
        if (keys == null || keys.isEmpty()) {
            return result;
        }
        ensureContext();
        if (sResolver != null && !sResolverGetBroken) {
            try {
                for (String k : keys) {
                    String v = Settings.Global.getString(sResolver, k);
                    result.put(k, v == null ? "null" : sanitizeSingleLine(v));
                }
                return result;
            } catch (SecurityException se) {
                sResolverGetBroken = true;
                result.clear();
            } catch (Throwable t) {
                result.clear();
            }
        }
        if (keys.size() == 1) {
            String k = keys.get(0);
            result.put(k, getGlobalSettingViaCli(k));
            return result;
        }
        List<String> lines;
        try {
            lines = execCommandAndReadLines("cmd", "settings", "list", "global");
        } catch (Throwable t) {
            lines = execCommandAndReadLines("settings", "list", "global");
        }
        Map<String, String> all = new HashMap<>();
        for (String raw : lines) {
            int eq = raw.indexOf('=');
            if (eq <= 0) {
                continue;
            }
            String k = raw.substring(0, eq).trim();
            String v = sanitizeSingleLine(raw.substring(eq + 1));
            all.put(k, v);
        }
        for (String k : keys) {
            String v = all.get(k);
            result.put(k, v == null ? "null" : v);
        }
        return result;
    }

    private static String getGlobalSetting(String key) throws Exception {
        ensureContext();
        if (sResolver != null && !sResolverGetBroken) {
            try {
                String v = Settings.Global.getString(sResolver, key);
                return v == null ? "null" : sanitizeSingleLine(v);
            } catch (SecurityException se) {
                sResolverGetBroken = true;
            } catch (Throwable ignored) {
            }
        }
        return getGlobalSettingViaCli(key);
    }

    private static void putGlobalSetting(String key, String value) throws SecurityException {
        ensureContext();
        if (sResolver == null || sResolverPutBroken) {
            throw new SecurityException("PUT_DENIED:" + key + ":no_resolver");
        }
        try {
            boolean ok = Settings.Global.putString(sResolver, key, value);
            if (!ok) {
                sResolverPutBroken = true;
                throw new SecurityException("PUT_DENIED:" + key + ":returned_false");
            }
        } catch (SecurityException se) {
            sResolverPutBroken = true;
            String msg = se.getMessage() == null ? "security_exception" : sanitizeSingleLine(se.getMessage());
            if (!msg.startsWith("PUT_DENIED:")) {
                msg = "PUT_DENIED:" + key + ":" + msg;
            }
            throw new SecurityException(msg, se);
        } catch (Throwable t) {
            throw new SecurityException(
                    "PUT_FAILED:" + key + ":" + t.getClass().getSimpleName() + ":" + sanitizeSingleLine(t.getMessage()),
                    t);
        }
    }

    private static String getDeviceModel() {
        if (sCachedModel != null && sCachedModel.length() > 0) {
            return sCachedModel;
        }
        try {
            Class<?> spClass = Class.forName("android.os.SystemProperties");
            String val = (String) spClass.getMethod("get", String.class, String.class)
                    .invoke(null, "ro.product.model", Build.MODEL);
            sCachedModel = sanitizeSingleLine(val);
        } catch (Throwable t) {
            sCachedModel = sanitizeSingleLine(Build.MODEL);
        }
        return sCachedModel;
    }

    private static void refreshPq() throws Exception {
        ensureContext();
        if (sContext != null && !sSendBroadcastBroken) {
            Intent intent = new Intent(PIC_MODE_CHANGED_ACTION);
            intent.putExtra("picmode", 7);
            try {
                Class<?> uhClass = Class.forName("android.os.UserHandle");
                Object userAll = uhClass.getField("ALL").get(null);
                Method sendAsUser = sContext.getClass().getMethod("sendBroadcastAsUser", Intent.class, uhClass);
                sendAsUser.invoke(sContext, intent, userAll);
                return;
            } catch (Throwable asUserErr) {
                try {
                    sContext.sendBroadcast(intent);
                    return;
                } catch (SecurityException sendSec) {
                    sSendBroadcastBroken = true;
                } catch (Throwable ignored) {
                }
            }
        }
        execCommandAndReadFirstLine(
                "am", "broadcast", "-a", PIC_MODE_CHANGED_ACTION, "--ei", "picmode", "7");
    }

    private static String[] splitWhitespace(String line) {
        String trimmed = line == null ? "" : line.trim();
        if (trimmed.length() == 0) {
            return new String[0];
        }
        return trimmed.split("\\s+");
    }

    private static void handleQuery(BufferedReader in, BufferedWriter out) throws Exception {
        List<String> settingKeys = new ArrayList<>();
        List<String> jniKeys = new ArrayList<>();
        List<String> flags = new ArrayList<>();

        boolean sawEnd = false;
        String line;
        while ((line = in.readLine()) != null) {
            String trimmed = line.trim();
            if ("END".equals(trimmed)) {
                sawEnd = true;
                break;
            }
            String[] parts = splitWhitespace(trimmed);
            if (parts.length == 0) {
                continue;
            }
            String sec = parts[0];
            if ("SETTINGS".equals(sec)) {
                for (int i = 1; i < parts.length; i++) {
                    settingKeys.add(parts[i]);
                }
            } else if ("JNI".equals(sec)) {
                for (int i = 1; i < parts.length; i++) {
                    jniKeys.add(parts[i]);
                }
            } else if ("FLAGS".equals(sec)) {
                for (int i = 1; i < parts.length; i++) {
                    flags.add(parts[i]);
                }
            }
        }
        if (!sawEnd) {
            out.write("ERR TRUNCATED_QUERY\n");
            out.write("END\n");
            out.flush();
            return;
        }

        long startMs = System.currentTimeMillis();
        out.write("OK\n");

        if (!settingKeys.isEmpty()) {
            try {
                Map<String, String> bulkSettings = getGlobalSettingsBulk(settingKeys);
                for (String k : settingKeys) {
                    String v = bulkSettings.get(k);
                    out.write("S " + k + "=" + (v == null ? "null" : v) + "\n");
                }
            } catch (Throwable bulkErr) {
                for (String k : settingKeys) {
                    try {
                        String v = getGlobalSetting(k);
                        out.write("S " + k + "=" + v + "\n");
                    } catch (Throwable t) {
                        out.write("S " + k + "=__ERR__:" + sanitizeSingleLine(t.getMessage()) + "\n");
                    }
                }
            }
        }

        for (String k : jniKeys) {
            try {
                int v = getJniValue(k);
                out.write("J " + k + "=" + v + "\n");
            } catch (Throwable t) {
                out.write("J " + k + "=__ERR__:" + t.getClass().getSimpleName() + "\n");
            }
        }

        for (String flag : flags) {
            try {
                if ("model".equals(flag)) {
                    out.write("F model=" + getDeviceModel() + "\n");
                } else if ("volume".equals(flag)) {
                    out.write("F volume=" + getMediaVolume() + "\n");
                } else if ("muted".equals(flag)) {
                    out.write("F muted=" + (getMediaMuted() ? "1" : "0") + "\n");
                } else if ("sound_out".equals(flag)) {
                    out.write("F sound_out=" + querySoundOutputSummary() + "\n");
                } else {
                    out.write("F " + flag + "=__ERR__:unknown_flag\n");
                }
            } catch (Throwable t) {
                out.write("F " + flag + "=__ERR__:" + sanitizeSingleLine(t.getMessage()) + "\n");
            }
        }

        out.write("ELAPSED " + (System.currentTimeMillis() - startMs) + "\n");
        out.write("END\n");
        out.flush();
    }

    private static boolean isPutLine(String raw) {
        return "PUT".equals(raw)
                || (raw.length() > 3 && raw.startsWith("PUT") && Character.isWhitespace(raw.charAt(3)));
    }

    private static String[] parsePutOp(String raw) {
        int len = raw.length();
        if (len <= 3 || !Character.isWhitespace(raw.charAt(3))) {
            return null;
        }
        int pos = 4;
        while (pos < len && Character.isWhitespace(raw.charAt(pos))) {
            pos++;
        }
        if (pos >= len) {
            return null;
        }
        int keyEnd = pos;
        while (keyEnd < len && !Character.isWhitespace(raw.charAt(keyEnd))) {
            keyEnd++;
        }
        String key = raw.substring(pos, keyEnd);
        String val = keyEnd + 1 <= len ? raw.substring(keyEnd + 1) : "";
        return new String[]{key, val};
    }

    private static void handleBatch(BufferedReader in, BufferedWriter out) throws Exception {
        List<String> ops = new ArrayList<>();
        boolean sawEnd = false;
        String line;
        while ((line = in.readLine()) != null) {
            String trimmed = line.trim();
            if ("END".equals(trimmed)) {
                sawEnd = true;
                break;
            }
            if (trimmed.length() > 0) {
                ops.add(line);
            }
        }
        if (!sawEnd) {
            out.write("ERR TRUNCATED_BATCH\n");
            out.write("END\n");
            out.flush();
            return;
        }

        long startMs = System.currentTimeMillis();
        List<String> stepLines = new ArrayList<>();

        for (int idx = 0; idx < ops.size(); idx++) {
            String raw = ops.get(idx);

            try {
                if (isPutLine(raw)) {
                    String[] parsedPut = parsePutOp(raw);
                    if (parsedPut == null) {
                        stepLines.add("STEP " + idx + " ERR PUT:missing_key");
                        continue;
                    }
                    String key = parsedPut[0];
                    String val = parsedPut[1];
                    try {
                        putGlobalSetting(key, val);
                        stepLines.add("STEP " + idx + " OK PUT " + key);
                    } catch (SecurityException se) {
                        out.write("ERR_AT " + idx + " " + sanitizeSingleLine(se.getMessage()) + "\n");
                        for (String sl : stepLines) {
                            out.write(sl + "\n");
                        }
                        out.write("END\n");
                        out.flush();
                        return;
                    }
                } else {
                    String[] parts = splitWhitespace(raw);
                    String op = parts.length > 0 ? parts[0] : "";
                    if ("JNI_SET".equals(op) && parts.length >= 3) {
                        int val = Integer.parseInt(parts[2]);
                        int upd = parts.length >= 4 ? Integer.parseInt(parts[3]) : 3;
                        int ret = setJniValue(parts[1], val, upd);
                        stepLines.add("STEP " + idx + " OK JNI_SET " + parts[1] + "=" + ret);
                    } else if ("JNI_RAMP".equals(op) && parts.length >= 4) {
                        int from = Integer.parseInt(parts[2]);
                        int to = Integer.parseInt(parts[3]);
                        int upd = parts.length >= 5 ? Integer.parseInt(parts[4]) : 3;
                        int stepMs = parts.length >= 6 ? Integer.parseInt(parts[5]) : 0;
                        int stepSize = parts.length >= 7 ? Integer.parseInt(parts[6]) : 1;
                        int applied = rampJni(parts[1], from, to, upd, stepMs, null, stepSize);
                        stepLines.add("STEP " + idx + " OK JNI_RAMP " + parts[1] + "=" + applied);
                    } else if ("COLOR_GAINS".equals(op) && parts.length >= 4) {
                        int ret = setColorGains(
                                Integer.parseInt(parts[1]),
                                Integer.parseInt(parts[2]),
                                Integer.parseInt(parts[3]));
                        stepLines.add("STEP " + idx + " OK COLOR_GAINS " + ret);
                    } else if ("HDR_TM".equals(op) && parts.length >= 2) {
                        int val = Integer.parseInt(parts[1]);
                        int upd = parts.length >= 3 ? Integer.parseInt(parts[2]) : 3;
                        int[] rets = setHdrToneMapping(val, upd);
                        stepLines.add("STEP " + idx + " OK HDR_TM " + rets[0]);
                    } else if ("SOUND_OUT".equals(op) && parts.length >= 2) {
                        int devId = Integer.parseInt(parts[1]);
                        setSoundOutput(devId);
                        stepLines.add("STEP " + idx + " OK SOUND_OUT " + devId);
                    } else if ("LED".equals(op) && parts.length >= 2) {
                        int ret = runColorfulLed(parts, 1);
                        stepLines.add("STEP " + idx + " OK LED " + ret);
                    } else if ("VOL_SET".equals(op) && parts.length >= 2) {
                        int val = Integer.parseInt(parts[1]);
                        boolean unmuteFirst = parts.length >= 3 && Integer.parseInt(parts[2]) != 0;
                        boolean showUi = parts.length >= 4 && Integer.parseInt(parts[3]) != 0;
                        boolean ok = setMediaVolume(val, unmuteFirst, showUi);
                        stepLines.add("STEP " + idx + (ok ? " OK VOL_SET " + val : " ERR VOL_VERIFY_FAILED"));
                    } else if ("VOL_MUTE".equals(op) && parts.length >= 2) {
                        boolean muted = Integer.parseInt(parts[1]) != 0;
                        boolean showUi = parts.length >= 3 && Integer.parseInt(parts[2]) != 0;
                        boolean ok = setMediaMuted(muted, showUi);
                        stepLines.add("STEP " + idx + (ok ? " OK VOL_MUTE " + muted : " ERR VOL_VERIFY_FAILED"));
                    } else if ("KEY".equals(op) && parts.length >= 2) {
                        execCommandAndReadFirstLine("input", "keyevent", parts[1]);
                        stepLines.add("STEP " + idx + " OK KEY " + parts[1]);
                    } else if ("SLEEP".equals(op) && parts.length >= 2) {
                        long ms = Math.max(0L, Math.min(5000L, Long.parseLong(parts[1])));
                        if (ms > 0) {
                            Thread.sleep(ms);
                        }
                        stepLines.add("STEP " + idx + " OK SLEEP " + ms);
                    } else if ("REFRESH_PQ".equals(op)) {
                        refreshPq();
                        stepLines.add("STEP " + idx + " OK REFRESH_PQ");
                    } else if ("SET_SOURCE".equals(op) && parts.length >= 2) {
                        int sourceId = Integer.parseInt(parts[1]);
                        setInputSource(sourceId);
                        stepLines.add("STEP " + idx + " OK SET_SOURCE " + sourceId);
                    } else {
                        stepLines.add("STEP " + idx + " ERR UNKNOWN_OP:" + op);
                    }
                }
            } catch (Throwable t) {
                stepLines.add("STEP " + idx + " ERR " + t.getClass().getSimpleName()
                        + ":" + sanitizeSingleLine(t.getMessage()));
            }
        }

        out.write("OK\n");
        for (String sl : stepLines) {
            out.write(sl + "\n");
        }
        out.write("ELAPSED " + (System.currentTimeMillis() - startMs) + "\n");
        out.write("END\n");
        out.flush();
    }

    private static void sendQuitToExistingDaemon(int port) {
        Socket client = null;
        try {
            client = new Socket();
            client.connect(new InetSocketAddress("127.0.0.1", port), 500);
            client.setSoTimeout(1000);
            BufferedWriter out = new BufferedWriter(
                    new OutputStreamWriter(client.getOutputStream(), StandardCharsets.UTF_8));
            BufferedReader in = new BufferedReader(
                    new InputStreamReader(client.getInputStream(), StandardCharsets.UTF_8));
            out.write("QUIT\n");
            out.flush();
            in.readLine();
        } catch (Throwable ignored) {
        } finally {
            try {
                if (client != null) {
                    client.close();
                }
            } catch (Throwable ignored) {
            }
        }
    }

    private static final int MAX_SUBSCRIBERS = 4;
    private static final long HEARTBEAT_INTERVAL_MS = 20000L;
    private static final long COALESCE_WINDOW_MS = 40L;
    private static final long DIFF_DEBOUNCE_MS = 120L;
    private static final String SELF_JAR_PATH = "/data/data/mitv.service/cache/MtkDirectTool.jar";
    private static final String SETTINGS_GLOBAL_DIR = "/data/system/users/0";
    private static final String[] WATCH_JNI_DIFF_KEYS = {
            "g_video__picture_mode",
            "g_disp__disp_back_light",
            "g_video__vid_local_dimming",
            "g_video__vid_gamut_mapping_mode",
            "g_video__clr_temp",
            "g_video__vid_hdr_tone_mapping_mode",
            "g_video__light_sensor_switch",
            "g_video__vid_mjc_effect",
            "g_video__freesync_switch",
            "g_video__dp_adaptive_sync"
    };
    private static final String[] DEFAULT_WATCH_KEYS = {
            "picture_mode",
            "picture_preset_scenario",
            "power_picture_off",
            "picture_backlight",
            "xiaomi_picture_backlight",
            "picture_brightness",
            "picture_contrast",
            "picture_saturation",
            "picture_hue",
            "picture_sharpness",
            "picture_color_temperature",
            "picture_red_gain",
            "picture_green_gain",
            "picture_blue_gain",
            "picture_local_dimming",
            "tv_picture_video_local_dimming",
            "picture_hdr_tone_mapping",
            "settings_display_hdr_color_tone",
            "picture_dynamic_definition",
            "tv_picture_mjc_effect",
            "tv_picture_game_mjc_effect",
            "picture_response_time",
            "tv_picture_advanced_video_color_space",
            "tv_picture_video_color_space",
            "tv_picture_light_sensor",
            "front_sight_index",
            "mt_game_dynamic_ft",
            "mt_game_scope",
            "mt_game_scope_night",
            "monitor_menu_fps_counter",
            "monitor_menu_stopwatch",
            "monitor_menu_timer",
            "mitv.tvplayer.hdmi.last.source",
            "xiaomi_sound_output_device",
            "atmosphere_light_switcher_pm2",
            "atmosphere_light_illumination",
            "atmosphere_light_color_temp",
            "atmosphere_light_color_value"
    };

    private static final Object sWatchLock = new Object();
    private static final List<SubscriberClient> sSubscribers = new ArrayList<>();
    private static HandlerThread sWatchThread;
    private static Handler sWatchHandler;
    private static Object sTvWatchBridge;
    private static Method sTvWatchBridgeClose;
    private static FileObserver sSettingsFileObserver;
    private static final Map<String, String> sLastWatchSnapshot = new HashMap<>();
    private static volatile String sLastWatchDiag = "init";
    private static volatile long sEventCount = 0;

    private static final Runnable sDiffSnapshotTask = new Runnable() {
        @Override
        public void run() {
            evaluateWatchedDiffs();
        }
    };

    private static final class SubscriberClient {
        final Socket socket;
        final BufferedReader in;
        final BufferedWriter out;
        final Set<String> keys;
        final Object lock = new Object();
        final LinkedHashMap<String, String> pending = new LinkedHashMap<>();
        boolean closed = false;

        SubscriberClient(Socket socket, BufferedReader in, BufferedWriter out, Set<String> keys) {
            this.socket = socket;
            this.in = in;
            this.out = out;
            this.keys = keys;
        }

        void enqueue(String key, String value) {
            if (!key.startsWith("jni:") && !keys.contains(key)) {
                return;
            }
            synchronized (lock) {
                if (closed) {
                    return;
                }
                pending.put(key, value == null ? "null" : value);
                lock.notifyAll();
            }
        }

        void markClosed() {
            synchronized (lock) {
                if (closed) {
                    return;
                }
                closed = true;
                pending.clear();
                lock.notifyAll();
            }
            try {
                socket.close();
            } catch (Throwable ignored) {
            }
        }
    }

    private static boolean canWatchEvents() {
        return true;
    }

    private static void scheduleWatchDiff() {
        sEventCount++;
        Handler h;
        synchronized (sWatchLock) {
            if (sSubscribers.isEmpty() || sWatchHandler == null) {
                return;
            }
            h = sWatchHandler;
        }
        h.removeCallbacks(sDiffSnapshotTask);
        h.postDelayed(sDiffSnapshotTask, DIFF_DEBOUNCE_MS);
    }

    private static Map<String, String> captureWatchSnapshot(Set<String> keys) {
        Map<String, String> snap = new LinkedHashMap<>();
        if (keys != null && !keys.isEmpty()) {
            try {
                Map<String, String> bulk = getGlobalSettingsBulk(new ArrayList<>(keys));
                snap.putAll(bulk);
            } catch (Throwable ignored) {
            }
        }
        try {
            ensureTvNative();
            for (String jk : WATCH_JNI_DIFF_KEYS) {
                try {
                    int jv = getJniValue(jk);
                    snap.put("jni:" + jk, String.valueOf(jv));
                } catch (Throwable ignoredJni) {
                }
            }
        } catch (Throwable ignoredTv) {
        }
        return snap;
    }

    private static void evaluateWatchedDiffs() {
        List<SubscriberClient> subsSnapshot;
        Set<String> unionKeys = new LinkedHashSet<>();
        synchronized (sWatchLock) {
            if (sSubscribers.isEmpty()) {
                return;
            }
            subsSnapshot = new ArrayList<>(sSubscribers);
            for (SubscriberClient sub : subsSnapshot) {
                unionKeys.addAll(sub.keys);
            }
        }
        Map<String, String> current = captureWatchSnapshot(unionKeys);
        if (current.isEmpty()) {
            return;
        }
        Map<String, String> diffs = new LinkedHashMap<>();
        synchronized (sWatchLock) {
            for (Map.Entry<String, String> e : current.entrySet()) {
                String k = e.getKey();
                String v = e.getValue();
                String prev = sLastWatchSnapshot.get(k);
                if (prev != null && !prev.equals(v)) {
                    diffs.put(k, v);
                }
                sLastWatchSnapshot.put(k, v);
            }
        }
        if (diffs.isEmpty()) {
            return;
        }
        for (SubscriberClient sub : subsSnapshot) {
            for (Map.Entry<String, String> d : diffs.entrySet()) {
                sub.enqueue(d.getKey(), d.getValue());
            }
        }
    }

    private static int activeWatcherCountLocked() {
        int count = 0;
        if (sTvWatchBridge != null) {
            count++;
        }
        if (sSettingsFileObserver != null) {
            count++;
        }
        return count;
    }

    @SuppressWarnings("deprecation")
    private static void syncWatchRegistrationsLocked() {
        if (sSubscribers.isEmpty()) {
            if (sWatchHandler != null) {
                sWatchHandler.removeCallbacks(sDiffSnapshotTask);
            }
            if (sTvWatchBridge != null && sTvWatchBridgeClose != null) {
                try {
                    sTvWatchBridgeClose.invoke(sTvWatchBridge);
                } catch (Throwable ignored) {
                }
            }
            sTvWatchBridge = null;
            sTvWatchBridgeClose = null;
            if (sSettingsFileObserver != null) {
                try {
                    sSettingsFileObserver.stopWatching();
                } catch (Throwable ignored) {
                }
                sSettingsFileObserver = null;
            }
            sLastWatchSnapshot.clear();
            return;
        }

        if (sWatchThread == null || !sWatchThread.isAlive()) {
            sWatchThread = new HandlerThread("mtk-watch-looper");
            sWatchThread.setDaemon(true);
            sWatchThread.start();
            sWatchHandler = new Handler(sWatchThread.getLooper());
        }

        StringBuilder diag = new StringBuilder();
        if (sTvWatchBridge == null) {
            try {
                ClassLoader tvLoader = ensureTvClassLoader();
                ClassLoader bridgeLoader = (ClassLoader) Class.forName("dalvik.system.PathClassLoader")
                        .getConstructor(String.class, ClassLoader.class)
                        .newInstance(SELF_JAR_PATH, tvLoader);
                Class<?> bridgeClass = bridgeLoader.loadClass("MtkTvWatchBridge");
                Runnable cb = new Runnable() {
                    @Override
                    public void run() {
                        scheduleWatchDiff();
                    }
                };
                sTvWatchBridge = bridgeClass.getConstructor(Runnable.class).newInstance(cb);
                sTvWatchBridgeClose = bridgeClass.getMethod("close");
                diag.append("hal=1");
            } catch (Throwable t) {
                Throwable root = (t instanceof java.lang.reflect.InvocationTargetException && t.getCause() != null)
                        ? t.getCause()
                        : t;
                diag.append("halErr=").append(root.getClass().getSimpleName());
            }
        } else {
            diag.append("hal=1");
        }

        if (sSettingsFileObserver == null) {
            try {
                File dir = new File(SETTINGS_GLOBAL_DIR);
                if (dir.isDirectory() && dir.canRead()) {
                    int mask = FileObserver.CLOSE_WRITE | FileObserver.MOVED_TO | FileObserver.MODIFY;
                    FileObserver fo = new FileObserver(SETTINGS_GLOBAL_DIR, mask) {
                        @Override
                        public void onEvent(int event, String path) {
                            if (path == null || path.contains("settings_global")) {
                                scheduleWatchDiff();
                            }
                        }
                    };
                    fo.startWatching();
                    sSettingsFileObserver = fo;
                    diag.append(",fs=1");
                } else {
                    diag.append(",fs=noread");
                }
            } catch (Throwable t) {
                diag.append(",fsErr=").append(t.getClass().getSimpleName());
            }
        } else {
            diag.append(",fs=1");
        }
        sLastWatchDiag = diag.toString();

        final Set<String> seedKeys = new LinkedHashSet<>();
        for (SubscriberClient sub : sSubscribers) {
            seedKeys.addAll(sub.keys);
        }
        sWatchHandler.post(new Runnable() {
            @Override
            public void run() {
                Map<String, String> initial = captureWatchSnapshot(seedKeys);
                synchronized (sWatchLock) {
                    for (Map.Entry<String, String> e : initial.entrySet()) {
                        if (!sLastWatchSnapshot.containsKey(e.getKey())) {
                            sLastWatchSnapshot.put(e.getKey(), e.getValue());
                        }
                    }
                }
            }
        });
    }

    private static void removeSubscriber(SubscriberClient sub) {
        if (sub == null) {
            return;
        }
        sub.markClosed();
        synchronized (sWatchLock) {
            sSubscribers.remove(sub);
            syncWatchRegistrationsLocked();
        }
    }

    private static void closeAllSubscribers() {
        List<SubscriberClient> snapshot;
        synchronized (sWatchLock) {
            snapshot = new ArrayList<>(sSubscribers);
            sSubscribers.clear();
            syncWatchRegistrationsLocked();
        }
        for (SubscriberClient sub : snapshot) {
            sub.markClosed();
        }
    }

    private static boolean handleSubscribe(Socket client, BufferedReader in, BufferedWriter out) throws Exception {
        Set<String> requestedKeys = new LinkedHashSet<>();
        boolean sawEnd = false;
        String line;
        while ((line = in.readLine()) != null) {
            String trimmed = line.trim();
            if ("END".equals(trimmed)) {
                sawEnd = true;
                break;
            }
            String[] parts = splitWhitespace(trimmed);
            if (parts.length > 1 && "KEYS".equals(parts[0])) {
                for (int i = 1; i < parts.length; i++) {
                    if (parts[i].length() > 0) {
                        requestedKeys.add(parts[i]);
                    }
                }
            }
        }
        if (!sawEnd) {
            out.write("ERR TRUNCATED_SUBSCRIBE\n");
            out.write("END\n");
            out.flush();
            return false;
        }
        if (requestedKeys.isEmpty()) {
            Collections.addAll(requestedKeys, DEFAULT_WATCH_KEYS);
        }

        client.setSoTimeout(0);
        client.setKeepAlive(true);
        final SubscriberClient sub = new SubscriberClient(client, in, out, requestedKeys);
        SubscriberClient evicted = null;
        int watcherCount;
        synchronized (sWatchLock) {
            if (sSubscribers.size() >= MAX_SUBSCRIBERS) {
                evicted = sSubscribers.remove(0);
            }
            sSubscribers.add(sub);
            syncWatchRegistrationsLocked();
            watcherCount = activeWatcherCountLocked();
        }
        if (evicted != null) {
            evicted.markClosed();
        }
        if (watcherCount == 0) {
            removeSubscriber(sub);
            out.write("ERR WATCH_REGISTER_FAILED " + sLastWatchDiag + "\n");
            out.write("END\n");
            out.flush();
            return false;
        }

        out.write("OK SUBSCRIBED watchers=" + watcherCount + " keys=" + requestedKeys.size() + "\n");
        out.flush();

        Thread writer = new Thread(new Runnable() {
            @Override
            public void run() {
                try {
                    while (true) {
                        Map<String, String> batch = null;
                        synchronized (sub.lock) {
                            if (sub.closed) {
                                break;
                            }
                            if (sub.pending.isEmpty()) {
                                sub.lock.wait(HEARTBEAT_INTERVAL_MS);
                            }
                            if (sub.closed) {
                                break;
                            }
                            if (!sub.pending.isEmpty()) {
                                if (COALESCE_WINDOW_MS > 0) {
                                    sub.lock.wait(COALESCE_WINDOW_MS);
                                    if (sub.closed) {
                                        break;
                                    }
                                }
                                batch = new LinkedHashMap<>(sub.pending);
                                sub.pending.clear();
                            }
                        }
                        if (batch == null || batch.isEmpty()) {
                            sub.out.write("HEARTBEAT\n");
                            sub.out.flush();
                        } else {
                            for (Map.Entry<String, String> entry : batch.entrySet()) {
                                sub.out.write("EVENT " + entry.getKey() + "=" + entry.getValue() + "\n");
                            }
                            sub.out.flush();
                        }
                    }
                } catch (Throwable ignored) {
                } finally {
                    removeSubscriber(sub);
                }
            }
        }, "mtk-sub-writer");
        writer.setDaemon(true);

        Thread reader = new Thread(new Runnable() {
            @Override
            public void run() {
                try {
                    String cmdLine;
                    while ((cmdLine = sub.in.readLine()) != null) {
                        String trimmedCmd = cmdLine.trim();
                        if ("UNSUBSCRIBE".equals(trimmedCmd) || "QUIT".equals(trimmedCmd)) {
                            break;
                        }
                    }
                } catch (Throwable ignored) {
                } finally {
                    removeSubscriber(sub);
                }
            }
        }, "mtk-sub-reader");
        reader.setDaemon(true);

        writer.start();
        reader.start();
        return true;
    }

    private static void runDaemon(int port) throws Exception {
        ensureTvNative();
        ensureContext();

        ServerSocket server = null;
        for (int attempt = 0; attempt < 5; attempt++) {
            try {
                ServerSocket candidate = new ServerSocket();
                candidate.setReuseAddress(true);
                candidate.bind(new InetSocketAddress(InetAddress.getByName("127.0.0.1"), port));
                server = candidate;
                break;
            } catch (BindException be) {
                sendQuitToExistingDaemon(port);
                Thread.sleep(150);
            }
        }
        if (server == null) {
            throw new IllegalStateException("Failed to bind daemon socket on 127.0.0.1:" + port);
        }

        int pid = Process.myPid();
        int uid = Process.myUid();
        log("DAEMON_READY port=" + port + " pid=" + pid + " uid=" + uid);

        boolean running = true;
        while (running) {
            Socket client = null;
            try {
                client = server.accept();
                client.setTcpNoDelay(true);
                client.setSoTimeout(10000);
                BufferedReader in = new BufferedReader(
                        new InputStreamReader(client.getInputStream(), StandardCharsets.UTF_8));
                BufferedWriter out = new BufferedWriter(
                        new OutputStreamWriter(client.getOutputStream(), StandardCharsets.UTF_8));

                String firstLine = in.readLine();
                if (firstLine == null) {
                    continue;
                }
                String cmd = firstLine.trim();
                if ("PING".equals(cmd)) {
                    ensureContext();
                    String putCap = (sResolver != null && !sResolverPutBroken) ? "1" : "0";
                    String watchCap = canWatchEvents() ? "1" : "0";
                    int subCount;
                    int obsCount;
                    synchronized (sWatchLock) {
                        subCount = sSubscribers.size();
                        obsCount = activeWatcherCountLocked();
                    }
                    out.write("OK MtkDirectTool " + VERSION + " pid=" + pid + " uid=" + uid
                            + " put=" + putCap + " watch=" + watchCap
                            + " subs=" + subCount + " obs=" + obsCount + " ev=" + sEventCount
                            + " diag=" + sLastWatchDiag + "\n");
                    out.write("END\n");
                    out.flush();
                } else if ("QUIT".equals(cmd)) {
                    running = false;
                    try {
                        server.close();
                    } catch (Throwable ignoredClose) {
                    }
                    out.write("OK BYE\n");
                    out.write("END\n");
                    out.flush();
                } else if ("QUERY".equals(cmd)) {
                    handleQuery(in, out);
                } else if ("BATCH".equals(cmd)) {
                    handleBatch(in, out);
                } else if ("SUBSCRIBE".equals(cmd)) {
                    if (handleSubscribe(client, in, out)) {
                        client = null;
                    }
                } else {
                    out.write("ERR UNKNOWN_CMD " + sanitizeSingleLine(cmd) + "\n");
                    out.write("END\n");
                    out.flush();
                }
            } catch (Throwable t) {
                Log.w(TAG, "Daemon client error: " + t.getMessage());
            } finally {
                try {
                    if (client != null) {
                        client.close();
                    }
                } catch (Throwable ignored) {
                }
            }
        }
        closeAllSubscribers();
        try {
            server.close();
        } catch (Throwable ignored) {
        }
    }

    public static void main(String[] args) throws Exception {
        if (args.length == 0) {
            log("Usage: MtkDirectTool <get/set/ramp/batchGet/getMinMax/setColorGains/setHdrToneMapping/setSoundOutput/getSoundOutput/led/daemon/dump> [args]");
            System.exit(1);
            return;
        }

        String cmd = args[0];
        String key = args.length >= 2 ? args[1] : null;
        int exitCode = 0;
        try {
            if ("daemon".equals(cmd)) {
                int port = args.length >= 2 ? parseIntOr(args[1], DEFAULT_DAEMON_PORT) : DEFAULT_DAEMON_PORT;
                runDaemon(port);
                System.exit(0);
                return;
            }

            if ("led".equals(cmd)) {
                if (args.length < 2) {
                    log("ERROR: led requires <off|ambient|cycle|lighting|solid|raw> ...");
                    writeResult("ERROR");
                    System.exit(1);
                    return;
                }
                int ret = runColorfulLed(args, 1);
                log("RESULT: LED " + args[1] + " ret=" + ret);
                writeResult(String.valueOf(ret));
                System.exit(0);
                return;
            }

            if ("setSoundOutput".equals(cmd)) {
                if (args.length < 2) {
                    log("ERROR: setSoundOutput requires deviceId");
                    writeResult("ERROR");
                    System.exit(1);
                    return;
                }
                int deviceId = Integer.parseInt(args[1]);
                try {
                    setSoundOutput(deviceId);
                } catch (IllegalArgumentException iae) {
                    log("ERROR: Unsupported sound output deviceId: " + deviceId);
                    writeResult("ERROR: Unsupported deviceId " + deviceId);
                    System.exit(1);
                    return;
                }
                System.exit(0);
                return;
            }

            if ("getSoundOutput".equals(cmd)) {
                String summary = querySoundOutputSummary();
                writeResult(summary);
                System.exit(0);
                return;
            }

            if ("setColorGains".equals(cmd)) {
                if (args.length < 4) {
                    log("ERROR: setColorGains requires red green blue");
                    writeResult("ERROR");
                    System.exit(1);
                    return;
                }
                int red = Integer.parseInt(args[1]);
                int green = Integer.parseInt(args[2]);
                int blue = Integer.parseInt(args[3]);
                int ret = setColorGains(red, green, blue);
                log("RESULT: SET_COLOR_GAINS r=" + red + " g=" + green + " b=" + blue + ", return: " + ret);
                writeResult(String.valueOf(ret));
                System.exit(0);
                return;
            }

            ensureTvNative();

            if ("get".equals(cmd)) {
                if (key == null) {
                    log("ERROR: key is required for get");
                    writeResult("ERROR");
                    System.exit(1);
                    return;
                }
                int val = getJniValue(key);
                log("RESULT: GET " + key + " = " + val);
                writeResult(String.valueOf(val));

            } else if ("batchGet".equals(cmd)) {
                if (args.length < 2) {
                    log("ERROR: batchGet requires at least one key");
                    writeBatchResult("ERROR\n");
                    System.exit(1);
                    return;
                }
                batchGet(args, 1);

            } else if ("setSource".equals(cmd)) {
                if (args.length < 2) {
                    log("ERROR: setSource requires sourceId");
                    writeResult("ERROR");
                    System.exit(1);
                    return;
                }
                try {
                    int sourceId = Integer.parseInt(args[1]);
                    setInputSource(sourceId);
                    String res = "RESULT: SET_SOURCE sourceId=" + sourceId;
                    log(res);
                    writeResult("OK");
                    writeFile(SOURCE_RESULT_FILE, res + "\n");
                } catch (Exception e) {
                    writeResult("ERROR: " + e.getMessage());
                    writeFile(SOURCE_RESULT_FILE, "ERROR: " + e.getMessage() + "\n");
                    throw e;
                }

            } else if ("setHdrToneMapping".equals(cmd)) {
                if (args.length < 2) {
                    log("ERROR: setHdrToneMapping requires value [isUpdate]");
                    writeResult("ERROR");
                    System.exit(1);
                    return;
                }
                int hdrValue = Integer.parseInt(args[1]);
                int hdrIsUpdate = args.length >= 3 ? Integer.parseInt(args[2]) : 3;
                int[] rets = setHdrToneMapping(hdrValue, hdrIsUpdate);
                log("RESULT: SET_HDR_TONE_MAPPING value=" + hdrValue + ", configReturn: " + rets[0]
                        + ", hdrAttrReturn: " + (rets[1] == Integer.MIN_VALUE ? "N/A" : rets[1]));
                writeResult(String.valueOf(rets[0]));

            } else if ("set".equals(cmd)) {
                if (key == null) {
                    log("ERROR: key is required for set");
                    writeResult("ERROR");
                    System.exit(1);
                    return;
                }
                int value = args.length >= 3 ? Integer.parseInt(args[2]) : 0;
                int isUpdate = args.length >= 4 ? Integer.parseInt(args[3]) : 1;
                int ret = setJniValue(key, value, isUpdate);
                log("RESULT: SET " + key + " to " + value + " (isUpdate=" + isUpdate + "), return: " + ret);
                writeResult(String.valueOf(ret));

            } else if ("ramp".equals(cmd)) {
                // 平滑渐变：把 key 从 from 推到 to。
                // 显示器自带的亮度键就是这样一小步一小步走的，单次大步长会让面板过冲 / 闪。
                // from 由 App 侧给出（它的粘性预览链），刻意不回读设备 —— JNI 回读值偶发跳变会带偏。
                // step 控制每步跨度（App 会按 delta 算好，把总步数压在小范围），stepMs 为 0 时不等。
                if (key == null || args.length < 4) {
                    log("ERROR: ramp requires <key> <from> <to> [isUpdate] [stepMs] [doneFile] [step]");
                    writeResult("ERROR");
                    System.exit(1);
                    return;
                }
                int from = Integer.parseInt(args[2]);
                int target = Integer.parseInt(args[3]);
                int isUpdate = args.length >= 5 ? Integer.parseInt(args[4]) : 3;
                int stepMs = args.length >= 6 ? Integer.parseInt(args[5]) : 0;
                String doneFile = args.length >= 7 ? args[6] : null;
                int stepSize = args.length >= 8 ? Math.max(1, Integer.parseInt(args[7])) : 1;
                long rampStartMs = System.currentTimeMillis();
                int applied = rampJni(key, from, target, isUpdate, stepMs, doneFile, stepSize);
                log("RESULT: RAMP " + key + " " + from + " -> " + target + " isUpdate=" + isUpdate
                        + " stepMs=" + stepMs + " elapsed=" + (System.currentTimeMillis() - rampStartMs) + "ms");
                writeResult(String.valueOf(applied));

            } else if ("getMinMax".equals(cmd)) {
                if (key == null) {
                    log("ERROR: key is required for getMinMax");
                    writeResult("ERROR");
                    System.exit(1);
                    return;
                }
                int minVal = (Integer) sGetMinMaxNative.invoke(null, 0, key);
                int maxVal = (Integer) sGetMinMaxNative.invoke(null, 1, key);
                String result = minVal + "," + maxVal;
                log("RESULT: RANGE " + key + " = [" + minVal + ", " + maxVal + "]");
                writeResult(result);

            } else if ("dump".equals(cmd)) {
                StringBuilder sb = new StringBuilder();
                String[] keys = {
                        "g_disp__disp_back_light",
                        "g_video__brightness",
                        "g_video__contrast",
                        "g_video__vid_sat",
                        "g_video__vid_hue",
                        "g_video__vid_shp",
                        "g_video__vid_local_dimming",
                        "g_video__clr_temp",
                        "g_video__clr_gain_r",
                        "g_video__clr_gain_g",
                        "g_video__clr_gain_b",
                        "g_video__vid_mjc_effect",
                        "g_video__vid_dejudder"
                };
                for (String k : keys) {
                    try {
                        int val = getJniValue(k);
                        log(k + ": " + val);
                        sb.append(k).append("=").append(val).append("\n");
                    } catch (Exception e) {
                        log(k + ": ERROR (" + e.getMessage() + ")");
                        sb.append(k).append("=ERROR\n");
                    }
                }
                writeResult(sb.toString());

            } else {
                log("ERROR: Unknown command " + cmd);
                writeResult("ERROR: Unknown command " + cmd);
                exitCode = 1;
            }
        } catch (Exception e) {
            log("FATAL ERROR: " + e.getMessage());
            writeResult("FATAL: " + e.getMessage());
            e.printStackTrace();
            exitCode = 1;
        }

        System.exit(exitCode);
    }
}
