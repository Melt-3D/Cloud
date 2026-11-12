<template>
  <widget-template>
    <template #title>{{ $t("Temperature Controls") }}</template>
    <template #content>
      <slot name="content">
        <div class="wrapper">
          <template v-if="show">
            <temperature-item
              v-for="(item, key) in temperatures"
              :key="key"
              :temp-key="key"
              :temp-item="item"
              :is-plugin-version-sufficient="isPluginVersionSufficient"
              @TempEditClicked="onEditClicked(key, item)"
            />
            <filament-sensor-item
              v-for="name in filamentSensorNames"
              :key="name"
              :sensor="filamentSensorStates[name]"
              :title="filamentSensorTitle(name)"
              :toggling="filamentSensorToggling === name"
              @toggle="toggleFilamentSensor(name, $event)"
            />
            <b-button
              v-if="isPluginVersionSufficient"
              variant="outline-primary"
              class="custom-button"
              @click="onTemperaturePresetsClicked"
            >
              {{$t("Temperature Presets")}}
            </b-button>
          </template>
          <template v-else>
            <div class="text-center mt-4">
              <b-spinner></b-spinner>
              <p class="mt-2">{{ $t("Loading temperature...") }}</p>
            </div>
          </template>
        </div>
      </slot>
    </template>
  </widget-template>
</template>

<script>
import TempTargetEditor from '@src/components/printers/TempTargetEditor.vue'
import TempPresets from '@src/components/printers/TempPresets.vue'
import WidgetTemplate from '@src/components/printer-control/WidgetTemplate'
import get from 'lodash/get'
import { temperatureDisplayName } from '@src/lib/utils'
import TemperatureItem from '@src/components/printers/TemperatureItem.vue'
import FilamentSensorItem from '@src/components/printers/FilamentSensorItem.vue'

const FILAMENT_SENSOR_PREFIXES = ['filament_switch_sensor ', 'filament_motion_sensor ']
const FILAMENT_SENSOR_POLL_MS = 3000
// passThruToPrinter never calls back on a lost message, so don't wait on it forever
const FILAMENT_SENSOR_REQUEST_TIMEOUT_MS = 10000

const DEFAULT_TEMP_PRESETS = [
  { name: 'PLA', extruder: 210, bed: 60 },
  { name: 'PETG', extruder: 240, bed: 80 },
  { name: 'ABS', extruder: 250, bed: 105 },
  { name: 'ASA', extruder: 255, bed: 105 },
  { name: 'TPU', extruder: 225, bed: 50 },
  { name: 'Preheat', extruder: 150, bed: 60 },
]

export default {
  name: 'TemperatureWidget',

  components: {
    WidgetTemplate,
    TemperatureItem,
    FilamentSensorItem,
  },

  props: {
    printer: {
      type: Object,
      required: true,
    },
    printerComm: {
      type: Object,
      required: true,
    },
  },

  data() {
    return {
      // Klipper object names, e.g. "filament_switch_sensor runout". null = not discovered yet
      filamentSensorObjects: null,
      // { "<object name>": { enabled, filament_detected } }
      filamentSensorStates: {},
      filamentSensorPollTimer: null,
      filamentSensorRequestPending: false,
      // Object name of the sensor whose enable/disable command is in flight
      filamentSensorToggling: null,
      // Bumped on every toggle so a poll that started before it can't overwrite the new state
      filamentSensorStateGeneration: 0,
    }
  },

  computed: {
    filamentSensorNames() {
      // Only sensors we've received a state for, so the rows never render half-empty
      return (this.filamentSensorObjects || []).filter((name) => this.filamentSensorStates[name])
    },
    temperatures() {
      const temperatures = {}
      for (const [key, value] of Object.entries(get(this.printer, 'status.temperatures', {}))) {
        if (Boolean(value.actual) && !isNaN(value.actual)) {
          // Take out NaN, 0, null. Apparently printers like Prusa throws random temperatures here.
          temperatures[key] = value
        }
      }
      return temperatures
    },
    isPluginVersionSufficient() {
      // If temp_profiles is missing, it's a plugin version too old to change temps
      return get(this.printer, 'settings.temp_profiles') != undefined
    },
    show() {
      return Object.keys(this.temperatures).length > 0
    },
    tempProfiles() {
      const printerProfiles = get(this.printer, 'settings.temp_profiles', [])
      const printerNames = new Set(printerProfiles.map((p) => p.name))
      return [...printerProfiles, ...DEFAULT_TEMP_PRESETS.filter((p) => !printerNames.has(p.name))]
    },
  },

  mounted() {
    this.pollFilamentSensors()
    this.filamentSensorPollTimer = setInterval(this.pollFilamentSensors, FILAMENT_SENSOR_POLL_MS)
  },

  beforeDestroy() {
    clearInterval(this.filamentSensorPollTimer)
  },

  methods: {
    temperatureDisplayName,
    moonrakerRequest(func, kwargs) {
      return new Promise((resolve, reject) => {
        const timeout = setTimeout(
          () => reject(new Error('timeout')),
          FILAMENT_SENSOR_REQUEST_TIMEOUT_MS
        )
        this.printerComm.passThruToPrinter(
          { target: 'moonraker_api', func, kwargs },
          (err, ret) => {
            clearTimeout(timeout)
            if (err || ret?.error) {
              reject(err || ret.error)
            } else {
              resolve(ret)
            }
          }
        )
      })
    },
    async pollFilamentSensors() {
      if (
        this.filamentSensorRequestPending ||
        document.hidden ||
        this.printer.isOffline() ||
        !this.printerComm.canSend()
      ) {
        return
      }
      // Discovery found no sensors on this printer: nothing to poll
      if (this.filamentSensorObjects !== null && this.filamentSensorObjects.length === 0) {
        return
      }

      this.filamentSensorRequestPending = true
      try {
        if (this.filamentSensorObjects === null) {
          const ret = await this.moonrakerRequest('printer/objects/list')
          this.filamentSensorObjects = (ret?.objects || []).filter((name) =>
            FILAMENT_SENSOR_PREFIXES.some((prefix) => name.startsWith(prefix))
          )
          if (this.filamentSensorObjects.length === 0) {
            return
          }
        }

        const query = {}
        this.filamentSensorObjects.forEach((name) => {
          query[name] = 'enabled,filament_detected'
        })
        const generation = this.filamentSensorStateGeneration
        const ret = await this.moonrakerRequest('printer/objects/query', query)
        if (generation === this.filamentSensorStateGeneration) {
          this.filamentSensorStates = ret?.status || {}
        }
      } catch (e) {
        // Klipper not ready, printer briefly unreachable, etc. Retry on the next tick, but
        // drop stale state so we never show "Loaded" for a sensor we can't actually read.
        this.filamentSensorStates = {}
      } finally {
        this.filamentSensorRequestPending = false
      }
    },
    async toggleFilamentSensor(objectName, enable) {
      const state = this.filamentSensorStates[objectName]
      if (!state || this.filamentSensorToggling) {
        return
      }
      // Klipper registers the sensor under the last word of its config section name
      const sensorName = objectName.split(' ').pop()

      this.filamentSensorToggling = objectName
      this.filamentSensorStateGeneration++
      // Update right away so the switch doesn't snap back while the command is in flight
      state.enabled = enable
      try {
        await this.moonrakerRequest('printer/gcode/script', {
          script: `SET_FILAMENT_SENSOR SENSOR=${sensorName} ENABLE=${enable ? 1 : 0}`,
        })
      } catch (e) {
        state.enabled = !enable
        this.$swal.Toast.fire({
          icon: 'error',
          title: `${this.$i18next.t('Failed to change filament sensor')}: ${e?.message || e}`,
        })
      } finally {
        this.filamentSensorToggling = null
        this.filamentSensorStateGeneration++
        this.pollFilamentSensors()
      }
    },
    filamentSensorTitle(objectName) {
      if (this.filamentSensorObjects.length === 1) {
        return this.$t('Filament')
      }
      // "filament_switch_sensor runout_sensor" -> "Runout sensor"
      const name = objectName.split(' ').slice(1).join(' ').replace(/_/g, ' ')
      return name.charAt(0).toUpperCase() + name.slice(1)
    },
    onEditClicked(key, item) {
      if (!this.isPluginVersionSufficient || item.target === null) {
        return
      }

      let tempProfiles = this.tempProfiles
      let presets
      let maxTemp = 350

      if (key.search(/bed|chamber/) > -1) {
        maxTemp = 140
      }
      if (key.search(/tool/) > -1) {
        // OctoPrint uses 'extruder' for toolx heaters
        presets = tempProfiles.map((v) => {
          return { name: v.name, target: v['extruder'] }
        })
      } else {
        presets = tempProfiles.map((v) => {
          return { name: v.name, target: v[key] }
        })
      }

      this.$swal
        .openModalWithComponent(
          TempTargetEditor,
          {
            presets: presets,
            maxTemp: maxTemp,
            curTarget: item.target,
          },
          {
            title: 'Set ' + temperatureDisplayName(key) + ' Temperature',
            confirmButtonText: 'Confirm',
            showCancelButton: true,
            preConfirm: () => {
              return {
                target: parseInt(document.getElementById('target-temp').value),
              }
            },
          }
        )
        .then((result) => {
          if (result.value) {
            let targetTemp = result.value.target
            this.handleSetTemp(key, targetTemp)
          }
        })
    },
    onTemperaturePresetsClicked() {
      let tempProfiles = this.tempProfiles

      this.$swal
        .openModalWithComponent(
          TempPresets,
          {
            presets: tempProfiles,
            printer: this.printer,
          },
          {
            title: `${this.$i18next.t('Temperature Presets')}`,
            confirmButtonText: 'Apply',
            showCancelButton: true,
            preConfirm: () => {
              return {
                preset: document.getElementById('selected-preset').value,
              }
            },
          }
        )
        .then((result) => {
          if (result.value?.preset) {
            this.applyTempPreset(result.value.preset)
          }
        })
    },
    applyTempPreset(preset) {
      let tempProfiles = this.tempProfiles
      let profile = tempProfiles.find((p) => p.name === preset)

      const presetObj = {}

      if (!profile) {
        if (preset === 'OFF') {
          presetObj.bed = 0
          presetObj.extruder = 0
          presetObj.chamber = 0
        } else {
          return
        }
      } else {
        Object.entries(profile).forEach((p) => {
          const presetName = p[0]
          if (presetName.toLowerCase().includes('bed')) {
            presetObj.bed = p[1]
          } else if (
            presetName.toLowerCase().includes('tool') ||
            presetName.toLowerCase().includes('extruder')
          ) {
            presetObj.extruder = p[1]
          } else if (presetName.toLowerCase().includes('chamber')) {
            presetObj.chamber = p[1]
          }
        })
      }

      const temperatures = get(this.printer, 'status.temperatures', [])
      Object.entries(temperatures).forEach((t) => {
        const toolName = t[0]
        if (toolName.toLowerCase().includes('bed')) {
          this.handleSetTemp(toolName, presetObj.bed)
        } else if (
          toolName.toLowerCase().includes('tool') ||
          toolName.toLowerCase().includes('extruder')
        ) {
          this.handleSetTemp(toolName, presetObj.extruder)
        } else if (toolName.toLowerCase().includes('chamber') && presetObj.chamber) {
          this.handleSetTemp(toolName, presetObj.chamber)
        }
      })
    },
    handleSetTemp(name, temp) {
      this.printerComm.passThruToPrinter({
        func: 'set_temperature',
        target: '_printer',
        args: [name, temp],
      })
    },
  },
}
</script>

<style lang="sass" scoped>
.wrapper
  display: flex
  flex-direction: column
  align-items: center
  gap: .825rem
  padding-bottom: 1rem
</style>
