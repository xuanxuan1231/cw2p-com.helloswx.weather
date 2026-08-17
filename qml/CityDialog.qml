import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import RinUI

Dialog {
    id: root

    property var backend
    property var info

    title: qsTr("选择城市")
    modal: true
    implicitWidth: 560

    readonly property bool coordinatesSupported: info?.supportsCoordinates ?? false
    readonly property bool hasLocalCityList: info?.hasCityList ?? false
    
    property bool useCoordinateInput: false
    property var currentSelection: null
    property var availableCities: []
    property bool searchInProgress: false
    property string searchText: ""

    // 计算属性：在 availableCities 基础上同时应用省份筛选和文字过滤
    property var filteredCities: {
        let cities = root.availableCities

        // 省份过滤
        if (root.hasLocalCityList && provinceSelector.currentIndex > 0) {
            const pid = filterProvinceList.get(provinceSelector.currentIndex).id
            cities = cities.filter(c => c.province_id === pid)
        }

        // 本地列表：支持汉字、全拼、首字母缩写三种匹配
        if (root.hasLocalCityList && root.searchText.trim() !== "") {
            const q = root.searchText.trim()
            const ql = q.toLowerCase()
            cities = cities.filter(c =>
                (c.name       && c.name.indexOf(q)         >= 0) ||
                (c.pinyin     && c.pinyin.indexOf(ql)      >= 0) ||
                (c.pinyin_abbr && c.pinyin_abbr.indexOf(ql) >= 0)
            )
        }

        return cities
    }

    ListModel { id: filterProvinceList }

    onOpened: initialize()

    function initialize() {
        resetInputs()
        setupProvinces()
    }

    // 不能叫 reset()——Dialog 自带同名 signal，会变成非法重写
    function resetInputs() {
        // 输入模式必须在每次打开时按 info 重算：Component.onCompleted 时 info 还是 {}
        useCoordinateInput = coordinatesSupported && info?.locationMode === "coordinates"
        // PillButton 是 checkable 的，点一下就会写掉 checked 上的绑定，
        // 所以这里显式回填一次，交给 ButtonGroup 保证互斥。
        if (useCoordinateInput) {
            coordinatePill.checked = true
        } else {
            cityPill.checked = true
        }

        currentSelection = null
        availableCities = []
        searchInProgress = false
        // 先停掉防抖，否则清空 searchBox 触发的那次 executeSearch 会在 280ms 后
        // 把用户刚点的城市清掉
        searchDebounceTimer.stop()
        searchText = ""
        searchBox.text = ""
        latitudeInput.text = info?.latitude ? String(info.latitude) : ""
        longitudeInput.text = info?.longitude ? String(info.longitude) : ""
    }

    function setupProvinces() {
        filterProvinceList.clear()
        filterProvinceList.append({ displayName: qsTr("全部"), id: -1 })

        if (hasLocalCityList && backend) {
            const provinces = backend.provinces()
            for (let i = 0; i < provinces.length; i++) {
                filterProvinceList.append({ displayName: provinces[i], id: i })
            }
            provinceSelector.currentIndex = 0
            // 默认"全部"：加载所有城市
            availableCities = backend.citiesIn(-1)
        }
    }

    function fetchCitiesForProvince(provinceId) {
        if (!backend || !hasLocalCityList) return

        // provinceId < 0 表示"全部"
        availableCities = backend.citiesIn(provinceId)
        currentSelection = null
        cityListView.currentIndex = -1
    }

    function executeSearch() {
        if (!backend) return

        // 本地列表：文字过滤由 filteredCities 计算属性实时处理，无需后端调用
        if (hasLocalCityList) return

        const query = searchBox.text.trim()

        if (query === "") {
            searchInProgress = false
            availableCities = []
            return
        }

        searchInProgress = true
        backend.searchCities(query)
    }

    function getDisplayMessage() {
        if (searchInProgress) {
            return qsTr("正在搜索…")
        }

        const count = root.filteredCities.length
        if (count === 0) {
            if (!hasLocalCityList && root.searchText.trim() === "") {
                return qsTr("输入城市名开始搜索")
            }
            return qsTr("没有匹配的城市")
        }

        return qsTr("共 %1 个结果").arg(count)
    }

    readonly property bool canConfirm: {
        if (useCoordinateInput) {
            const lat = parseFloat(latitudeInput.text)
            const lon = parseFloat(longitudeInput.text)
            return !isNaN(lat) && !isNaN(lon)
        }
        return currentSelection !== null
    }

    Connections {
        target: root.backend
        enabled: root.backend !== null && root.backend !== undefined

        function onCitiesFound(query, results) {
            if (!root.backend) return
            // 只认最后一次输入的结果，忽略迟到的旧请求
            if (query !== searchBox.text.trim()) return

            root.searchInProgress = false
            root.availableCities = results ?? []
            root.currentSelection = null
            cityListView.currentIndex = -1
        }
    }

    Timer {
        id: searchDebounceTimer
        interval: 280
        onTriggered: root.executeSearch()
    }

    ColumnLayout {
        Layout.fillWidth: true
        spacing: 16

        RowLayout {
            Layout.fillWidth: true
            spacing: 8
            visible: coordinatesSupported

            ButtonGroup {
                id: inputModeGroup
                exclusive: true
            }

            PillButton {
                id: cityPill
                ButtonGroup.group: inputModeGroup
                text: qsTr("城市")
                onClicked: root.useCoordinateInput = false
            }

            PillButton {
                id: coordinatePill
                ButtonGroup.group: inputModeGroup
                text: qsTr("经纬度")
                onClicked: root.useCoordinateInput = true
            }

            Item { Layout.fillWidth: true }
        }

        ColumnLayout {
            Layout.fillWidth: true
            spacing: 10
            visible: !root.useCoordinateInput

            RowLayout {
                Layout.fillWidth: true
                spacing: 12

                ComboBox {
                    id: provinceSelector
                    Layout.preferredWidth: 150
                    visible: hasLocalCityList
                    model: filterProvinceList
                    textRole: "displayName"
                    onActivated: {
                        root.currentSelection = null
                        cityListView.currentIndex = -1
                        // filteredCities 会自动按新省份过滤，无需手动加载
                        // 但 availableCities 仍需是"全部"，以便文字过滤能跨省搜索
                    }
                }

                TextField {
                    id: searchBox
                    Layout.fillWidth: true
                    placeholderText: hasLocalCityList ? qsTr("搜索城市") : qsTr("搜索城市，例如「柏林」")
                    onTextChanged: {
                        root.searchText = text
                        if (!root.hasLocalCityList) {
                            searchDebounceTimer.restart()
                        }
                    }
                }
            }

            Text {
                Layout.fillWidth: true
                typography: Typography.Caption
                color: Theme.currentTheme.colors.textSecondaryColor
                text: root.getDisplayMessage()
            }



            Frame {
                Layout.fillWidth: true
                Layout.preferredHeight: 300

                ListView {
                    id: cityListView
                    anchors.fill: parent
                    clip: true
                    model: root.filteredCities
                    currentIndex: -1

                    delegate: ListViewDelegate {
                        required property var modelData
                        required property int index
                        
                        width: ListView.view.width
                        text: modelData.name || ""
                        highlighted: cityListView.currentIndex === index

                        onClicked: {
                            cityListView.currentIndex = index
                            root.currentSelection = modelData
                        }
                    }

                    ScrollBar.vertical: ScrollBar {
                        policy: ScrollBar.AsNeeded
                    }
                }
            }
        }

        ColumnLayout {
            Layout.fillWidth: true
            spacing: 10
            visible: root.useCoordinateInput

            Text {
                Layout.fillWidth: true
                typography: Typography.Caption
                color: Theme.currentTheme.colors.textSecondaryColor
                wrapMode: Text.Wrap
                text: qsTr("纬度范围 -90 ~ 90，经度范围 -180 ~ 180。可在地图应用中长按获取坐标。")
            }

            RowLayout {
                Layout.fillWidth: true
                spacing: 16

                ColumnLayout {
                    Layout.fillWidth: true
                    spacing: 8

                    Text {
                        text: qsTr("纬度")
                        typography: Typography.Body
                    }

                    TextField {
                        id: latitudeInput
                        Layout.fillWidth: true
                        placeholderText: "39.9042"
                        validator: DoubleValidator {
                            bottom: -90
                            top: 90
                            decimals: 6
                            notation: DoubleValidator.StandardNotation
                        }
                    }
                }

                ColumnLayout {
                    Layout.fillWidth: true
                    spacing: 8

                    Text {
                        text: qsTr("经度")
                        typography: Typography.Body
                    }

                    TextField {
                        id: longitudeInput
                        Layout.fillWidth: true
                        placeholderText: "116.4074"
                        validator: DoubleValidator {
                            bottom: -180
                            top: 180
                            decimals: 6
                            notation: DoubleValidator.StandardNotation
                        }
                    }
                }
            }
        }
    }

    footer: DialogButtonBox {
        standardButtons: DialogButtonBox.Ok | DialogButtonBox.Cancel

        property Button confirmButton: standardButton(DialogButtonBox.Ok)

        Component.onCompleted: {
            confirmButton.enabled = Qt.binding(() => root.canConfirm)
        }

        onAccepted: {
            if (!backend) {
                root.close()
                return
            }

            if (root.useCoordinateInput) {
                const lat = parseFloat(latitudeInput.text)
                const lon = parseFloat(longitudeInput.text)
                backend.setCoordinates(lat, lon, "")
            } else if (root.currentSelection) {
                backend.setCity(
                    root.currentSelection.code ?? "",
                    root.currentSelection.name ?? "",
                    parseFloat(root.currentSelection.latitude ?? 0) || 0,
                    parseFloat(root.currentSelection.longitude ?? 0) || 0
                )
            }

            root.close()
        }

        onRejected: {
            root.close()
        }
    }
}
