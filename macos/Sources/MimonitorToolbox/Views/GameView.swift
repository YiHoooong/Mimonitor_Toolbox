import SwiftUI

struct GameView: View {
    @EnvironmentObject var state: AppState

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 16) {
                PageHeader(title: "游戏模式") { state.forceRefreshPage("game") }

                Text(state.gameModeHintText)
                    .font(.system(size: 12))
                    .foregroundColor(state.gameModeHintIsWarning
                                     ? Color(red: 0.94, green: 0.72, blue: 0.35)
                                     : .secondary)

                ButtonGroupSection(title: "准星", options: OptionLists.crosshair,
                                   selectedValue: state.intValue("front_sight_index", default: 0)) { v in
                    state.setCrosshair(v)
                }

                ButtonGroupSection(title: "动态准星", options: OptionLists.dynamicCrosshair,
                                   selectedValue: state.intValue("mt_game_dynamic_ft", default: 0)) { v in
                    state.setGameFeature(key: "mt_game_dynamic_ft", value: v,
                                         message: "动态准星: \(v == 0 ? "关" : "开")")
                }

                ButtonGroupSection(title: "狙击镜", options: OptionLists.scope,
                                   selectedValue: state.intValue("mt_game_scope", default: 0)) { v in
                    state.setGameFeature(key: "mt_game_scope", value: v,
                                         message: "狙击镜: \(v == 0 ? "关" : "\(v)")")
                }

                ButtonGroupSection(title: "狙击镜夜视", options: OptionLists.scopeNight,
                                   selectedValue: state.intValue("mt_game_scope_night", default: 0)) { v in
                    state.setGameFeature(key: "mt_game_scope_night", value: v,
                                         message: "狙击镜夜视: \(v == 0 ? "关" : "开")")
                }

                ButtonGroupSection(title: "320Hz竞技模式", options: OptionLists.mode320,
                                   selectedValue: state.intValue("mode_320", default: 0)) { v in
                    state.setMode320(v == 1)
                }

                ButtonGroupSection(title: "FreeSync Premium Pro", options: OptionLists.freesync,
                                   selectedValue: state.intValue("freesync", default: 0)) { v in
                    state.setFreesync(v == 1)
                }

                ButtonGroupSection(title: "FPS计数器", options: OptionLists.fpsCounter,
                                   selectedValue: state.intValue("monitor_menu_fps_counter", default: 0)) { v in
                    let name = v == 0 ? "关" : (v == 1 ? "刷新率" : "柱状图")
                    state.setGameFeature(key: "monitor_menu_fps_counter", value: v, message: "FPS: \(name)")
                }

                ButtonGroupSection(title: "秒表", options: OptionLists.stopwatch,
                                   selectedValue: state.intValue("monitor_menu_stopwatch", default: 0)) { v in
                    state.setGameFeature(key: "monitor_menu_stopwatch", value: v,
                                         message: "秒表: \(v == 0 ? "关" : "开")")
                }

                ButtonGroupSection(title: "定时器", options: OptionLists.timer,
                                   selectedValue: state.intValue("monitor_menu_timer", default: 0)) { v in
                    state.setGameFeature(key: "monitor_menu_timer", value: v, message: "定时器: \(v)")
                }
            }
            .padding(30)
        }
        .onAppear { state.refreshPage("game") }
        .loadingOverlay(state.loadingPages.contains("game"))
    }
}
