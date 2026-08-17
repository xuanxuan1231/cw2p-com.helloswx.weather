import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import RinUI
import ClassWidgets.Plugins

SettingsLayout {
    id: layout

    SettingCard {
        Layout.fillWidth: true
        icon.name: "ic_fluent_temperature_20_regular"
        title: qsTr("主体显示")
        description: qsTr("主体屏幕展示实时温度，或当日最高 / 最低温")

        ComboBox {
            id: contentModeBox
            Layout.preferredWidth: 180
            property var values: ["current", "high_low"]
            model: ListModel {
                ListElement { text: qsTr("实时温度") }
                ListElement { text: qsTr("最高 / 最低温") }
            }
            onActivated: settings.content_mode = values[currentIndex]
            Component.onCompleted: currentIndex = Math.max(0, values.indexOf(settings.content_mode || "current"))
        }
    }

    SettingCard {
        Layout.fillWidth: true
        icon.name: "ic_fluent_clock_20_regular"
        title: qsTr("加入「未来 3 小时」屏幕")
        description: qsTr("部分数据源没有逐小时预报，此时该屏幕不会出现")

        Switch {
            id: hourlySwitch
            onCheckedChanged: settings.show_hourly = checked
            Component.onCompleted: checked = settings.show_hourly === true
        }
    }

    SettingCard {
        Layout.fillWidth: true
        icon.name: "ic_fluent_slide_multiple_20_regular"
        title: qsTr("轮播降水预报与天气预警")
        description: qsTr("关闭后只保留主体屏幕，降水与预警仅显示为右上角摘要")

        Switch {
            id: carouselSwitch
            onCheckedChanged: settings.carousel = checked
            Component.onCompleted: checked = settings.carousel !== false
        }
    }

    SettingCard {
        Layout.fillWidth: true
        visible: carouselSwitch.checked
        icon.name: "ic_fluent_timer_20_regular"
        title: qsTr("主体屏幕停留时长")
        description: qsTr("优先级最高，停留时间应长于次级屏幕（秒）")

        SpinBox {
            Layout.preferredWidth: 150
            from: 4
            to: 120
            stepSize: 1
            onValueModified: settings.main_seconds = value
            Component.onCompleted: value = settings.main_seconds || 12
        }
    }

    SettingCard {
        Layout.fillWidth: true
        visible: carouselSwitch.checked
        icon.name: "ic_fluent_warning_20_regular"
        title: qsTr("降水 / 预警屏幕停留时长")
        description: qsTr("次级屏幕的停留时间（秒）")

        SpinBox {
            Layout.preferredWidth: 150
            from: 2
            to: 60
            stepSize: 1
            onValueModified: settings.detail_seconds = value
            Component.onCompleted: value = settings.detail_seconds || 6
        }
    }
}
