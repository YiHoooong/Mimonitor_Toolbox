import android.os.HwBinder;
import android.os.HwParcel;
import android.os.IHwBinder;

public class ColorfulLedTool {
    private static final String BSP_IFACE = "vendor.mi.hardware.bspmserver@2.0::IBspMServer";
    private static final String CONFIG_IFACE = "vendor.mi.hardware.bspmserver@2.0::IVendorConfig";

    private static void releaseQuietly(HwParcel parcel) {
        if (parcel != null) {
            try {
                parcel.release();
            } catch (Throwable ignored) {
            }
        }
    }

    private static IHwBinder getConfigBinder() throws Exception {
        IHwBinder bsp = HwBinder.getService(BSP_IFACE, "default");
        if (bsp == null) {
            throw new IllegalStateException("HwBinder.getService(" + BSP_IFACE + ") returned null");
        }
        HwParcel req = new HwParcel();
        HwParcel reply = new HwParcel();
        try {
            req.writeInterfaceToken(BSP_IFACE);
            bsp.transact(6, req, reply, 0);
            reply.verifySuccess();
            req.releaseTemporaryStorage();
            int status = reply.readInt32();
            IHwBinder config = reply.readStrongBinder();
            if (status != 0 || config == null) {
                throw new RuntimeException("CreateConfigInterface failed status=" + status + " binder=" + config);
            }
            return config;
        } finally {
            releaseQuietly(reply);
            releaseQuietly(req);
        }
    }

    private static int setColorfulLed(int mode, int color) throws Exception {
        IHwBinder config = getConfigBinder();
        HwParcel req = new HwParcel();
        HwParcel reply = new HwParcel();
        try {
            req.writeInterfaceToken(CONFIG_IFACE);
            req.writeInt32(mode);
            req.writeInt32(color);
            config.transact(2, req, reply, 0);
            reply.verifySuccess();
            req.releaseTemporaryStorage();
            return reply.readInt32();
        } finally {
            releaseQuietly(reply);
            releaseQuietly(req);
        }
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

    private static int clamp(int v, int lo, int hi) {
        return Math.max(lo, Math.min(hi, v));
    }

    private static int parse(String s, int def) {
        try {
            return Integer.parseInt(s);
        } catch (Exception e) {
            return def;
        }
    }

    static int[] resolveModeAndColor(String[] args, int offset) {
        if (args == null || offset >= args.length) {
            throw new IllegalArgumentException("Usage: ColorfulLedTool <off|ambient|cycle|lighting|solid|raw> ...");
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
            color = lightingColor(parse(arg1 != null ? arg1 : "9", 9), parse(arg2 != null ? arg2 : "1", 1));
        } else if ("solid".equals(cmd)) {
            mode = 1;
            color = solidColor(parse(arg1 != null ? arg1 : "9", 9), parse(arg2 != null ? arg2 : "0", 0));
        } else if ("raw".equals(cmd)) {
            mode = parse(arg1 != null ? arg1 : "0", 0);
            color = parse(arg2 != null ? arg2 : "0", 0);
        } else {
            throw new IllegalArgumentException("Unknown command: " + cmd);
        }
        return new int[]{mode, color};
    }

    public static void main(String[] args) throws Exception {
        if (args.length < 1) {
            System.err.println("Usage: ColorfulLedTool <off|ambient|cycle|lighting|solid|raw> ...");
            System.exit(1);
            return;
        }
        try {
            int[] pair = resolveModeAndColor(args, 0);
            int mode = pair[0];
            int color = pair[1];
            int ret = setColorfulLed(mode, color);
            System.out.println("SetColorfulLed mode=" + mode + " color=" + color + " ret=" + ret);
        } catch (IllegalArgumentException iae) {
            System.err.println("ERROR: " + iae.getMessage());
            System.exit(1);
        }
    }
}
