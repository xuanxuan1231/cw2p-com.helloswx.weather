import QtQuick
import RinUI

/*!
    小组件里的数字/文本。

    字重与「时间」小组件（ClassWidgets.Theme 的 Title）保持一致，
    逐项设置 font 子属性，避免整体绑定 font 对象时覆盖字号或字重。
*/
Text {
    id: numeral

    property int px: 36
    property int weight: Configs.data.preferences.font_weight || 600

    font.family: AppCentral.getQFont(Configs.data.preferences.font, Utils.fontFamily).family
    font.pixelSize: px
    font.weight: weight

    Behavior on px {
        NumberAnimation { duration: 400; easing.type: Easing.OutQuint }
    }
}
