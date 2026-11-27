import android.os.RemoteException;
import com.mediatek.twoworlds.tv.MtkTvTVCallbackHandler;

public class MtkTvWatchBridge extends MtkTvTVCallbackHandler {
    private static final String[] WATCH_CFG_IDS = {
            "g_video__picture_mode",
            "g_disp__disp_back_light",
            "g_video__brightness",
            "g_video__contrast",
            "g_video__vid_sat",
            "g_video__vid_hue",
            "g_video__vid_shp",
            "g_video__vid_local_dimming",
            "g_video__vid_gamut_mapping_mode",
            "g_video__clr_temp",
            "g_fusion_picture__pq_wb_cor_gain",
            "g_video__vid_hdr_tone_mapping_mode",
            "g_video__light_sensor_switch",
            "g_video__vid_mjc_effect",
            "g_video__vid_game_mjc_switch",
            "g_video__freesync_switch",
            "g_video__dp_adaptive_sync",
            "g_fusion_picture__hdmi_edid_version",
            "g_fusion_picture__dp_edid_version",
            "g_audio__audio only"
    };

    private final Runnable mCallback;

    public MtkTvWatchBridge(Runnable callback) {
        super();
        this.mCallback = callback;
        for (String cfgId : WATCH_CFG_IDS) {
            try {
                addListenerConfig(cfgId);
            } catch (Throwable ignored) {
            }
        }
    }

    private void fire() {
        Runnable cb = this.mCallback;
        if (cb != null) {
            try {
                cb.run();
            } catch (Throwable ignored) {
            }
        }
    }

    @Override
    public int notifyConfigMessage(int i, int i2) throws RemoteException {
        fire();
        return 0;
    }

    @Override
    public int notifyConfigValuechanged(String str) throws Exception {
        fire();
        return 0;
    }

    @Override
    public int notifyInputSourceMessage(int i, int i2, int i3, int i4) throws RemoteException {
        fire();
        return 0;
    }

    @Override
    public int notifyInputSignalChanged(int i, boolean z) throws RemoteException {
        fire();
        return 0;
    }

    @Override
    public int notifyAVModeMessage(int i, int i2, int i3, int i4) throws RemoteException {
        fire();
        return 0;
    }

    @Override
    public int notifyVideoInfoMessage(int i, int i2, int i3, int i4, int i5, int i6, int i7) throws RemoteException {
        fire();
        return 0;
    }

    public void close() {
        try {
            removeListener();
        } catch (Throwable ignored) {
        }
    }
}
