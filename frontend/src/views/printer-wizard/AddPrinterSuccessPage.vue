<template>
  <page-layout>
    <template #content>
      <div class="container">
        <div class="row">
          <div class="col-sm-12 p-4">

            <div class="form-container full-on-mobile border-radius-lg">
              <div v-if="verifiedPrinter" class="text-center py-5">
                <svg class="success-checkmark">
                  <use href="#svg-success-checkmark" />
                </svg>
                <h3 class="pb-4">{{ $t("Successfully linked to your account!") }}</h3>
                <div
                  class="col-sm-12 col-md-8 offset-md-2 col-lg-6 offset-lg-3 d-flex flex-column align-center justify-content-center"
                >
                  <saving-animation
                    :errors="errorMessages.printer_name"
                    :saving="saving.printer_name"
                  >
                    <div class="printer-name-input">
                      <div class="edit-icon">
                        <i class="fas fa-pen"></i>
                      </div>
                      <input
                        v-model="verifiedPrinter.name"
                        type="text"
                        class="dark"
                        :placeholder="$t('Printer name')"
                        @input="updatePrinterName"
                      />
                    </div>
                  </saving-animation>
                  <div>
                    <div class="text-muted mx-auto text-center font-weight-light">
                      {{$t("Give your printer a shiny name.")}}
                    </div>
                  </div>
                </div>
                <br /><br />
                <div
                  class="col-sm-12 col-md-8 offset-md-2 col-lg-6 offset-lg-3 d-flex flex-column align-center justify-content-center"
                >
                  <div v-if="redirectToTunnelCreation" class="mt-4">
                    <a
                      :href="redirectToTunnelCreation"
                      class="btn btn-primary btn-block mx-auto btn-lg"
                      >{{ $t("Authorize App Access") }}</a
                    >
                  </div>
                  <div v-else>
                    <div class="mt-4">
                      <a href="/printers/" class="btn btn-primary btn-block mx-auto btn-lg"
                        >{{ $t("Go Check Out Printer Feed!") }}</a
                      >
                    </div>
                    <!-- <div class="mt-5">
                      <a
                        href="/user_preferences/notification_twilio/"
                        class="btn btn-outline-secondary btn-block mx-auto"
                        >{{ $t("Add Phone Number") }}</a
                      >
                    </div>
                    <div>
                      <div class="text-muted mx-auto text-center font-weight-light">
                        {{$t("Receive text (SMS) in case of print failures.")}}
                      </div>
                    </div> -->
                    <div class="mt-4">
                      <a :href="editPrinterUrl" class="btn btn-outline-secondary btn-block mx-auto"
                        >{{ $t("Change Printer Settings") }}</a
                      >
                    </div>
                    <div>
                      <div class="text-muted mx-auto text-center font-weight-light">
                        {{$t("You can always change it later.")}}
                      </div>
                    </div>
                  </div>
                </div>
              </div>
            </div>
          </div>
        </div>
      </div>
    </template>
  </page-layout>
</template>

<script>
import axios from 'axios'

import PageLayout from '@src/components/PageLayout.vue'
import SavingAnimation from '@src/components/SavingAnimation.vue'
import urls from '@config/server-urls'

// Must match Printer.DEFAULT_NAME in backend/app/models/other_models.py
const DEFAULT_PRINTER_NAME = 'My Awesome Cloud Printer'
const NAME_POLL_INTERVAL_MS = 2000
const NAME_POLL_MAX_ATTEMPTS = 15

export default {
  components: {
    PageLayout,
    SavingAnimation,
  },
  data() {
    return {
      verifiedPrinter: null,
      nameEditedByUser: false,
      namePollTimeoutId: null,
      saving: {},
      errorMessages: {},
      delayedSubmit: {
        // Make pause before sending new value to API
        printer_name: {
          delay: 1000,
          timeoutId: null,
        },
      },
    }
  },
  computed: {
    redirectToTunnelCreation() {
      return new URLSearchParams(window.location.search).get('redirectToTunnelCreation')
    },
    editPrinterUrl() {
      return `/printers/${this.verifiedPrinter.id}/`
    },


  },
  created() {
    this.fetchPrinter()
  },
  beforeDestroy() {
    clearTimeout(this.namePollTimeoutId)
  },
  methods: {
    async fetchPrinter() {
      const response = await axios.get(urls.printer(this.$route.params.printerId))
      this.verifiedPrinter = response.data
      this.pollForHostnameName(NAME_POLL_MAX_ATTEMPTS)
    },
    // The backend renames the printer to its hostname once the agent sends its first status,
    // which may happen after this page has loaded. Pick up the new name unless the user is editing it.
    pollForHostnameName(attemptsLeft) {
      if (
        attemptsLeft <= 0 ||
        this.nameEditedByUser ||
        this.verifiedPrinter.name !== DEFAULT_PRINTER_NAME
      ) {
        return
      }
      this.namePollTimeoutId = setTimeout(async () => {
        try {
          const response = await axios.get(urls.printer(this.verifiedPrinter.id))
          if (!this.nameEditedByUser) {
            this.verifiedPrinter.name = response.data.name
          }
        } catch (e) {
          // Ignore, try again on the next attempt
        }
        this.pollForHostnameName(attemptsLeft - 1)
      }, NAME_POLL_INTERVAL_MS)
    },
    setSavingStatus(propName, status) {
      if (status) {
        delete this.errorMessages[propName]
      }
      this.$set(this.saving, propName, status)
    },
    updatePrinterName() {
      this.nameEditedByUser = true
      clearTimeout(this.namePollTimeoutId)
      if ('name' in this.verifiedPrinter && this.verifiedPrinter.name) {
        const delayInfo = this.delayedSubmit['printer_name']
        if (delayInfo['timeoutId']) {
          clearTimeout(delayInfo['timeoutId'])
        }
        this.delayedSubmit['printer_name']['timeoutId'] = setTimeout(() => {
          this.setSavingStatus('printer_name', true)
          // Make request to API
          return axios
            .patch(urls.printer(this.verifiedPrinter.id), {
              name: this.verifiedPrinter.name,
            })
            .then(() => {
              this.setSavingStatus('printer_name', false)
            })
            .catch((error) => {
              this.errorDialog(error, 'Failed to update printer name')
            })
        }, delayInfo['delay'])
        return
      } else {
        const delayInfo = this.delayedSubmit['printer_name']
        if (delayInfo['timeoutId']) {
          clearTimeout(delayInfo['timeoutId'])
        }
      }
    },
  },
}
</script>

<style lang="sass" scoped>
</style>
