<template>
  <div>
    <div>
      <h5>{{ $t("Presets") }}:</h5>
    </div>
    <div>
      <b-form-select id="id_preset" v-model="currentPreset" class="form-control">
        <b-form-select-option v-for="pre in allPresets" :key="pre.name" :value="pre.name">
          {{ pre.name }}
        </b-form-select-option>
      </b-form-select>
    </div>

    <div class="preset-details mt-3">
      <div v-if="currentPreset === 'OFF'" class="text-muted">
        {{ $t("Turns all heaters off.") }}
      </div>
      <div v-for="item in selectedPresetTemps" v-else :key="item.key" class="preset-detail-row">
        <span class="text-muted">{{ item.label }}</span>
        <span>{{ item.value > 0 ? `${item.value} °C` : $t("Off") }}</span>
      </div>
    </div>

    <muted-alert class="mt-4 mb-1">
      {{ $t('Temperature presets can be edited or added in {agentName} settings.',{agentName}) }}

    </muted-alert>

    <input id="selected-preset" v-model="currentPreset" type="hidden" />
  </div>
</template>

<script>
import MutedAlert from '@src/components/MutedAlert.vue'
import { temperatureDisplayName } from '@src/lib/utils'

export default {
  name: 'TempPresets',

  components: {
    MutedAlert,
  },

  props: {
    presets: {
      type: Array,
      required: true,
    },
    printer: {
      type: Object,
      required: true,
    },
  },

  data: function () {
    return {
      currentPreset: null,
    }
  },

  computed: {
    allPresets() {
      let presets = [...this.presets]
      presets.push({ value: 0, name: 'OFF' })
      return presets
    },
    selectedPresetTemps() {
      const preset = this.allPresets.find((p) => p.name === this.currentPreset)
      if (!preset) {
        return []
      }
      return Object.entries(preset)
        .filter(([key, value]) => key !== 'name' && typeof value === 'number')
        .map(([key, value]) => ({ key, label: temperatureDisplayName(key), value }))
    },
    agentName() {
      return this.printer.agentDisplayName()
    },
  },

  mounted() {
    this.currentPreset = this.allPresets[0].name
  },

  methods: {},
}
</script>
<style lang="sass" scoped>
.preset-details
  padding: .75rem 1rem
  border: 1px solid var(--color-divider, rgba(128, 128, 128, .3))
  border-radius: var(--border-radius-sm, 6px)

.preset-detail-row
  display: flex
  justify-content: space-between
  padding: .15rem 0
</style>

