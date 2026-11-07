<template>
  <div class="sensor-item">
    <div class="icon">
      <i class="fas fa-tape"></i>
    </div>
    <div class="title">{{ title }}</div>
    <div class="value" :class="stateClass">{{ stateLabel }}</div>
    <b-form-checkbox
      switch
      class="toggle"
      :checked="sensor.enabled"
      :disabled="toggling"
      :title="sensor.enabled ? $t('Disable sensor') : $t('Enable sensor')"
      @change="$emit('toggle', $event)"
    />
  </div>
</template>

<script>
export default {
  name: 'FilamentSensorItem',

  props: {
    // Klipper object state: { enabled: Boolean, filament_detected: Boolean }
    sensor: {
      type: Object,
      required: true,
    },
    title: {
      type: String,
      required: true,
    },
    toggling: {
      type: Boolean,
      default: false,
    },
  },

  computed: {
    stateClass() {
      if (!this.sensor.enabled) return 'disabled'
      return this.sensor.filament_detected ? 'detected' : 'runout'
    },
    stateLabel() {
      if (!this.sensor.enabled) return this.$t('Disabled')
      return this.sensor.filament_detected ? this.$t('Loaded') : this.$t('Runout')
    },
  },
}
</script>

<style lang="sass" scoped>
// Mirrors TemperatureItem so the rows line up in TemperatureWidget
.sensor-item
  display: flex
  width: 100%
  background-color: var(--color-background)
  padding: .8125rem
  border-radius: var(--border-radius-md)
  gap: 1rem
  align-items: center
.icon
  flex-shrink: 0
  width: 36px
  height: 36px
  display: flex
  align-items: center
  justify-content: center
  background-color: var(--color-primary)
  color: var(--color-on-primary)
  border-radius: var(--border-radius-sm)

.title
  white-space: nowrap
  overflow: hidden
  text-overflow: ellipsis

.value
  margin-left: auto
  flex-shrink: 0
  white-space: nowrap
  font-weight: bold
  &.detected
    color: var(--color-success)
  &.runout
    color: var(--color-danger)
  &.disabled
    color: var(--color-text-secondary)
    font-weight: normal

.toggle
  flex-shrink: 0
  // Bootstrap's custom-control adds a left gutter for the label; there's no label here
  margin-right: -.5rem
</style>
