import QtQuick

/*!
    Meteocons 图标（sprite sheet 版）。

    从预渲染的 sprite sheet PNG 中逐帧播放动画。
    使用 lottie-web + puppeteer 在构建时渲染，确保旋转等动画正确。
*/
Item {
    id: root

    property string path: ""
    property real box: 38
    property real contentScale: 1

    implicitWidth: box
    implicitHeight: box

    readonly property string slug: {
        if (!path) return ""
        var s = path.toString()
        var lastSlash = s.lastIndexOf("/")
        var filename = lastSlash >= 0 ? s.substring(lastSlash + 1) : s
        return filename.replace(".json", "")
    }

    // SpriteSequence 不支持运行时动态更换 source；用 Loader 在 slug 变化时
    // 强制销毁并重建组件，确保图标切换立即生效。
    Loader {
        id: spriteLoader
        anchors.centerIn: parent
        width: root.box * root.contentScale
        height: root.box * root.contentScale
        active: root.slug !== ""
        sourceComponent: spriteComponent
    }

    Component {
        id: spriteComponent
        SpriteSequence {
            width: spriteLoader.width
            height: spriteLoader.height
            interpolate: false
            goalSprite: ""

            Sprite {
                name: "anim"
                source: Qt.resolvedUrl("../../assets/sprite_sheets/" + root.slug + ".png")
                frameCount: 60
                frameWidth: 128
                frameHeight: 128
                frameDuration: 100
            }
        }
    }

    onSlugChanged: {
        spriteLoader.active = false
        spriteLoader.active = root.slug !== ""
    }
}
