import QtQuick
import Qt5Compat.GraphicalEffects

/*!
    小组件顶部的柔光。

    对应设计稿里贴着顶边的椭圆径向渐变：圆心在顶边中点，
    水平半径约为宽度的 0.785 倍，垂直半径 50。
*/
Item {
    id: root

    property color glowColor: "#F8AF18"
    property real glowAlpha: 0.15
    property real cornerRadius: height * 0.22

    RadialGradient {
        id: gradient
        anchors.fill: parent
        visible: false

        horizontalOffset: 0
        verticalOffset: -height / 2
        horizontalRadius: width * 0.785
        verticalRadius: 50

        gradient: Gradient {
            GradientStop { position: 0.0; color: Qt.alpha(root.glowColor, root.glowAlpha) }
            GradientStop { position: 1.0; color: Qt.alpha(root.glowColor, 0) }
        }
    }

    Rectangle {
        id: mask
        anchors.fill: parent
        radius: root.cornerRadius
        visible: false
    }

    OpacityMask {
        anchors.fill: parent
        source: gradient
        maskSource: mask
    }

    Behavior on glowColor {
        ColorAnimation { duration: 350; easing.type: Easing.OutQuint }
    }
}
