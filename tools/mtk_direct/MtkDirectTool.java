import android.util.Log;
import java.io.File;
import java.io.FileWriter;
import java.lang.reflect.Constructor;
import java.lang.reflect.Method;

public class MtkDirectTool {
    private static final String TAG = "MtkDirectTool";
    private static final String RESULT_FILE = "/data/data/mitv.service/cache/.jni_result";
    private static final String BATCH_RESULT_FILE =
            "/sdcard/Download/Mimonitor_Toolbox/.mtk_batch_result.txt";
    private static final String TV_CLASSPATH =
            "/system/framework/mitvmiddlewareimpl.jar:/system_ext/priv-app/TvServices/TvServices.apk";
    private static final String NATIVE_LIB_PATH = "/vendor/lib64:/system/lib64:/system_ext/lib64";

    private static void log(String msg) {
        System.out.println(msg);
        Log.d(TAG, msg);
    }

    private static void writeFile(String path, String value) {
        try {
            File file = new File(path);
            File parent = file.getParentFile();
            if (parent != null && !parent.exists()) {
                parent.mkdirs();
            }
            FileWriter fw = new FileWriter(file);
            fw.write(value);
            fw.close();
        } catch (Exception e) {
            Log.e(TAG, "Failed to write result file: " + e.getMessage());
        }
    }

    private static void writeResult(String value) {
        writeFile(RESULT_FILE, value);
    }

    private static void writeBatchResult(String value) {
        writeFile(BATCH_RESULT_FILE, value);
    }

    private static ClassLoader tvClassLoader() throws Exception {
        return (ClassLoader) Class.forName("dalvik.system.PathClassLoader")
                .getConstructor(String.class, String.class, ClassLoader.class)
                .newInstance(TV_CLASSPATH, NATIVE_LIB_PATH, ClassLoader.getSystemClassLoader());
    }

    private static void setColorGains(ClassLoader classLoader, int red, int green, int blue) throws Exception {
        Class<?> displayClass = classLoader.loadClass("com.mediatek.twoworlds.factory.MtkTvFApiDisplay");
        Object display = displayClass.getMethod("getInstance").invoke(null);

        Class<?> enumClass = classLoader.loadClass(
                "com.mediatek.twoworlds.factory.common.MtkTvFApiDisplayTypes$EnumColorTemperature");
        Object userColorTemp = Enum.valueOf((Class<Enum>) enumClass.asSubclass(Enum.class),
                "E_MTK_FAPI_COLOR_TEMP_USER");

        Class<?> dataClass = classLoader.loadClass(
                "com.mediatek.twoworlds.factory.common.MtkTvFApiDisplayTypes$ColorTempData");
        Constructor<?> ctor = dataClass.getConstructor(
                int.class, int.class, int.class, int.class, int.class, int.class);
        Object data = ctor.newInstance(red, green, blue, 1024, 1024, 1024);

        Method method = displayClass.getMethod("setWbGainOffsetEx", enumClass, dataClass, int.class);
        int ret = (Integer) method.invoke(display, userColorTemp, data, 0);
        log("RESULT: SET_COLOR_GAINS r=" + red + " g=" + green + " b=" + blue + ", return: " + ret);
        writeResult(String.valueOf(ret));
    }

    private static void setHdrToneMapping(ClassLoader classLoader, Method setConfigValueNative, int value,
            int isUpdate) throws Exception {
        int configRet = (Integer) setConfigValueNative.invoke(
                null, -1, "g_video__vid_hdr_tone_mapping_mode", value, isUpdate);
        Integer hdrAttrRet = null;
        try {
            Class<?> providerClass = classLoader.loadClass("xiaomi.hardware.mitv.provider.V1_0.IMiTVProvider");
            Object provider = providerClass.getMethod("getService", boolean.class).invoke(null, false);
            if (provider != null) {
                hdrAttrRet = (Integer) providerClass.getMethod("setHdrAttr", int.class, int.class)
                        .invoke(provider, 4, value);
            }
        } catch (Exception e) {
            log("WARN: setHdrAttr failed: " + e.getMessage());
        }
        log("RESULT: SET_HDR_TONE_MAPPING value=" + value + ", configReturn: " + configRet
                + ", hdrAttrReturn: " + (hdrAttrRet == null ? "N/A" : hdrAttrRet));
        writeResult(String.valueOf(configRet));
    }

    private static void batchGet(Method getConfigValueNative, String[] keys, int startIndex) {
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
                int val = (Integer) getConfigValueNative.invoke(null, -1, k);
                sb.append(k).append("=").append(val).append("\n");
            } catch (Throwable e) {
                sb.append(k).append("=ERROR:").append(e.getClass().getSimpleName()).append("\n");
            }
        }
        sb.append("__elapsed_ms=").append(System.currentTimeMillis() - startMs).append("\n");
        writeBatchResult(sb.toString());
        log("RESULT: BATCH_GET keys=" + count);
    }

    private static boolean transactTvService(int code, int arg) {
        Object data = null;
        Object reply = null;
        try {
            Class<?> smClass = Class.forName("android.os.ServiceManager");
            Object tvBinder = smClass.getMethod("getService", String.class).invoke(null, "TvService");
            if (tvBinder == null) {
                log("WARN: TvService binder is null for tx " + code);
                return false;
            }
            Class<?> iBinderClass = Class.forName("android.os.IBinder");
            Class<?> parcelClass = Class.forName("android.os.Parcel");
            Method obtain = parcelClass.getMethod("obtain");
            data = obtain.invoke(null);
            reply = obtain.invoke(null);
            parcelClass.getMethod("writeInterfaceToken", String.class).invoke(data, "mitv.internal.ITvService");
            parcelClass.getMethod("writeInt", int.class).invoke(data, arg);
            boolean ok = (Boolean) iBinderClass
                    .getMethod("transact", int.class, parcelClass, parcelClass, int.class)
                    .invoke(tvBinder, code, data, reply, 0);
            parcelClass.getMethod("readException").invoke(reply);
            return ok;
        } catch (Throwable t) {
            log("WARN: TvService tx " + code + " failed: " + t.getMessage());
            return false;
        } finally {
            try {
                if (reply != null) {
                    reply.getClass().getMethod("recycle").invoke(reply);
                }
            } catch (Throwable ignored) {
            }
            try {
                if (data != null) {
                    data.getClass().getMethod("recycle").invoke(data);
                }
            } catch (Throwable ignored) {
            }
        }
    }

    private static int queryTvServiceInt(int code) {
        Object data = null;
        Object reply = null;
        try {
            Class<?> smClass = Class.forName("android.os.ServiceManager");
            Object tvBinder = smClass.getMethod("getService", String.class).invoke(null, "TvService");
            if (tvBinder == null) {
                return -1;
            }
            Class<?> iBinderClass = Class.forName("android.os.IBinder");
            Class<?> parcelClass = Class.forName("android.os.Parcel");
            Method obtain = parcelClass.getMethod("obtain");
            data = obtain.invoke(null);
            reply = obtain.invoke(null);
            parcelClass.getMethod("writeInterfaceToken", String.class).invoke(data, "mitv.internal.ITvService");
            iBinderClass.getMethod("transact", int.class, parcelClass, parcelClass, int.class)
                    .invoke(tvBinder, code, data, reply, 0);
            parcelClass.getMethod("readException").invoke(reply);
            return (Integer) parcelClass.getMethod("readInt").invoke(reply);
        } catch (Throwable t) {
            log("WARN: TvService query tx " + code + " failed: " + t.getMessage());
            return -1;
        } finally {
            try {
                if (reply != null) {
                    reply.getClass().getMethod("recycle").invoke(reply);
                }
            } catch (Throwable ignored) {
            }
            try {
                if (data != null) {
                    data.getClass().getMethod("recycle").invoke(data);
                }
            } catch (Throwable ignored) {
            }
        }
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

    private static Object getAudioService() throws Exception {
        Class<?> smClass = Class.forName("android.os.ServiceManager");
        Object audioBinder = smClass.getMethod("getService", String.class).invoke(null, "audio");
        if (audioBinder == null) {
            throw new IllegalStateException("ServiceManager.getService(\"audio\") returned null");
        }
        Class<?> iBinderClass = Class.forName("android.os.IBinder");
        Class<?> stubClass = Class.forName("android.media.IAudioService$Stub");
        Object audioService = stubClass.getMethod("asInterface", iBinderClass).invoke(null, audioBinder);
        if (audioService == null) {
            throw new IllegalStateException("IAudioService$Stub.asInterface returned null");
        }
        return audioService;
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
                log("ERROR: Unsupported sound output deviceId: " + deviceId);
                writeResult("ERROR: Unsupported deviceId " + deviceId);
                return;
        }

        Class<?> audioSystemClass = Class.forName("android.media.AudioSystem");
        Method getParameters = audioSystemClass.getMethod("getParameters", String.class);
        Method setParameters = audioSystemClass.getMethod("setParameters", String.class);
        Method getOutputDeviceName = audioSystemClass.getMethod("getOutputDeviceName", int.class);

        Class<?> iAudioServiceClass = Class.forName("android.media.IAudioService");
        Object audioService = getAudioService();

        String spdifParam = (String) getParameters.invoke(null, "xmfeaspdifdet");
        boolean spdifSupported = "xmfeaspdifdet=support".equals(spdifParam);

        setParameters.invoke(null, "xiaomi_switch_device=true");
        int forceRet;
        int hdmiRet;
        boolean spdifDisconnected = false;
        int paramRet;
        String devName;
        try {
            forceRet = (Integer) iAudioServiceClass
                    .getMethod("xmSetForceUse", int.class, int.class)
                    .invoke(audioService, 1, forceConfig);
            hdmiRet = (Integer) iAudioServiceClass
                    .getMethod("setHdmiSystemAudioSupported", boolean.class)
                    .invoke(audioService, hdmiArc);
            if (!spdifSupported) {
                spdifDisconnected = disconnectSpdif(iAudioServiceClass, audioService);
            }
            devName = (String) getOutputDeviceName.invoke(null, androidDev);
            paramRet = (Integer) setParameters.invoke(null, "switch_output_device=" + devName);
        } finally {
            try {
                setParameters.invoke(null, "xiaomi_switch_device=false");
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

    private static void getSoundOutput() throws Exception {
        Class<?> audioSystemClass = Class.forName("android.media.AudioSystem");
        Method getParameters = audioSystemClass.getMethod("getParameters", String.class);
        Class<?> iAudioServiceClass = Class.forName("android.media.IAudioService");
        Object audioService = getAudioService();

        int forceUse = (Integer) iAudioServiceClass
                .getMethod("xmGetForceUse", int.class)
                .invoke(audioService, 1);
        boolean hdmiAudio = (Boolean) iAudioServiceClass
                .getMethod("isHdmiSystemAudioSupported")
                .invoke(audioService);
        String spdifParam = (String) getParameters.invoke(null, "xmfeaspdifdet");
        int tvDevice = queryTvServiceInt(34);

        log("RESULT: GET_SOUND_OUTPUT forceUseMedia=" + forceUse
                + " hdmiSystemAudio=" + hdmiAudio
                + " tvDevice=" + tvDevice
                + " spdif=" + spdifParam);
        writeResult(forceUse + "," + hdmiAudio + "," + tvDevice);
    }

    public static void main(String[] args) throws Exception {
        if (args.length == 0) {
            log("Usage: MtkDirectTool <get/set/ramp/batchGet/getMinMax/setColorGains/setHdrToneMapping/setSoundOutput/getSoundOutput/dump> [args]");
            return;
        }

        String cmd = args[0];
        String key = args.length >= 2 ? args[1] : null;

        try {
            if ("setSoundOutput".equals(cmd)) {
                if (args.length < 2) {
                    log("ERROR: setSoundOutput requires deviceId");
                    writeResult("ERROR");
                    return;
                }
                setSoundOutput(Integer.parseInt(args[1]));
                return;
            }

            if ("getSoundOutput".equals(cmd)) {
                getSoundOutput();
                return;
            }

            ClassLoader classLoader = tvClassLoader();

            if ("setColorGains".equals(cmd)) {
                if (args.length < 4) {
                    log("ERROR: setColorGains requires red green blue");
                    writeResult("ERROR");
                    return;
                }
                setColorGains(classLoader, Integer.parseInt(args[1]), Integer.parseInt(args[2]),
                        Integer.parseInt(args[3]));
                return;
            }

            Class<?> tvNativeClass = classLoader.loadClass("com.mediatek.twoworlds.tv.TVNative");
            Method getConfigValueNative = tvNativeClass.getDeclaredMethod(
                    "getConfigValue_native", int.class, String.class);
            getConfigValueNative.setAccessible(true);
            Method setConfigValueNative = tvNativeClass.getDeclaredMethod(
                    "setConfigValue_native", int.class, String.class, int.class, int.class);
            setConfigValueNative.setAccessible(true);
            Method getMinMaxNative = tvNativeClass.getDeclaredMethod(
                    "getMinMaxConfigValue_native", int.class, String.class);
            getMinMaxNative.setAccessible(true);

            if ("get".equals(cmd)) {
                if (key == null) {
                    log("ERROR: key is required for get");
                    writeResult("ERROR");
                    return;
                }
                int val = (Integer) getConfigValueNative.invoke(null, -1, key);
                log("RESULT: GET " + key + " = " + val);
                writeResult(String.valueOf(val));

            } else if ("batchGet".equals(cmd)) {
                if (args.length < 2) {
                    log("ERROR: batchGet requires at least one key");
                    writeBatchResult("ERROR\n");
                    return;
                }
                batchGet(getConfigValueNative, args, 1);

            } else if ("setHdrToneMapping".equals(cmd)) {
                if (args.length < 2) {
                    log("ERROR: setHdrToneMapping requires value [isUpdate]");
                    writeResult("ERROR");
                    return;
                }
                int hdrValue = Integer.parseInt(args[1]);
                int hdrIsUpdate = args.length >= 3 ? Integer.parseInt(args[2]) : 3;
                setHdrToneMapping(classLoader, setConfigValueNative, hdrValue, hdrIsUpdate);

            } else if ("set".equals(cmd)) {
                if (key == null) {
                    log("ERROR: key is required for set");
                    writeResult("ERROR");
                    return;
                }
                int value = args.length >= 3 ? Integer.parseInt(args[2]) : 0;
                int isUpdate = args.length >= 4 ? Integer.parseInt(args[3]) : 1;
                int ret = (Integer) setConfigValueNative.invoke(null, -1, key, value, isUpdate);
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
                    return;
                }
                int from = Integer.parseInt(args[2]);
                int target = Integer.parseInt(args[3]);
                int isUpdate = args.length >= 5 ? Integer.parseInt(args[4]) : 3;
                int stepMs = args.length >= 6 ? Integer.parseInt(args[5]) : 0;
                String doneFile = args.length >= 7 ? args[6] : null;
                int stepSize = args.length >= 8 ? Math.max(1, Integer.parseInt(args[7])) : 1;
                if (doneFile != null && doneFile.length() > 0) {
                    try {
                        new File(doneFile).delete();
                    } catch (Exception e) {
                        // ignore
                    }
                }
                long rampStartMs = System.currentTimeMillis();
                int applied = from;
                int v = from;
                int step = target >= from ? stepSize : -stepSize;
                while (true) {
                    setConfigValueNative.invoke(null, -1, key, v, isUpdate);
                    applied = v;
                    if (v == target) {
                        break;
                    }
                    int next = v + step;
                    if ((step > 0 && next > target) || (step < 0 && next < target)) {
                        next = target;
                    }
                    v = next;
                    if (stepMs > 0) {
                        Thread.sleep(stepMs);
                    }
                }
                log("RESULT: RAMP " + key + " " + from + " -> " + target + " isUpdate=" + isUpdate
                        + " stepMs=" + stepMs + " elapsed=" + (System.currentTimeMillis() - rampStartMs) + "ms");
                writeResult(String.valueOf(applied));
                // 渐变完成信号：App 侧等这个文件出现后才会写 settings，
                // 避免 settings 观察者提前把画面拉到目标值、把渐变打断。
                if (doneFile != null && doneFile.length() > 0) {
                    writeFile(doneFile, "1");
                }

            } else if ("getMinMax".equals(cmd)) {
                if (key == null) {
                    log("ERROR: key is required for getMinMax");
                    writeResult("ERROR");
                    return;
                }
                int minVal = (Integer) getMinMaxNative.invoke(null, 0, key);
                int maxVal = (Integer) getMinMaxNative.invoke(null, 1, key);
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
                        int val = (Integer) getConfigValueNative.invoke(null, -1, k);
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
            }
        } catch (Exception e) {
            log("FATAL ERROR: " + e.getMessage());
            writeResult("FATAL: " + e.getMessage());
            e.printStackTrace();
        }

        System.exit(0);
    }
}
