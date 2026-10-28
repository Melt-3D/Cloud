<template>
  <div
    class="page-wrapper"
    :class="{ collapsed: collapsed, 'is-in-mobile': inMobileWebView, 'is-in-popup': isPopup }"
  >
    <!-- Main view (with top-bar) -->
    <div class="content-wrapper" :class="{ 'hide-top-nav': hideTopNav }">
      <!-- Top-bar -->
      <b-navbar class="top-nav">
        <div class="top-nav-left">
          <a href="/" class="brand">
            <SyndicateAwareSVG href="#svg-logo-compact" width="100" height="30" />
          </a>
          <ul class="list-unstyled m-0 main-nav">
            <li v-for="item in mainNavItems" :key="item.key" :class="{ active: item.active }">
              <a :href="item.href" :class="{ primary: item.primary }">
                <svg v-if="item.svg" class="nav-svg-icon"><use :href="item.svg" /></svg>
                <font-awesome-icon v-else :icon="item.icon" />
                <span>{{ item.title }}</span>
              </a>
            </li>
          </ul>
          <div v-if="$slots.topBarLeft" class="page-title">
            <slot name="topBarLeft"></slot>
          </div>
        </div>
        <div class="top-nav-right">
          <slot name="topBarRight"></slot>
          <ul class="list-unstyled m-0 user-nav">
            <li v-for="item in userNavItems" :key="item.key" :class="{ active: item.active }">
              <a :href="item.href" :title="item.title">
                <font-awesome-icon :icon="item.icon" />
                <span v-if="item.badge" class="badge">{{ item.badge }}</span>
                <span class="sr-only">{{ item.title }}</span>
              </a>
            </li>
          </ul>
          <b-button
            variant="_"
            class="shadow-none p-0 position-relative toggle-sidebar"
            :aria-expanded="collapsed ? 'false' : 'true'"
            @click="collapsed = !collapsed"
          >
            <i class="fas fa-bars position-relative">
              <div v-if="hasUnseenPrinterEvents" class="notification-dot">
                <svg width="8px" height="8px">
                  <use href="#svg-circle-icon" />
                </svg>
              </div>
            </i>
          </b-button>
        </div>
      </b-navbar>
      <!-- Hamburger dropdown (narrow screens) -->
      <nav v-show="!collapsed" class="nav-dropdown">
        <ul class="list-unstyled m-0">
          <li
            v-for="item in [...mainNavItems, ...userNavItems]"
            :key="item.key"
            :class="{ active: item.active }"
          >
            <a :href="item.href" :class="{ primary: item.primary }">
              <svg v-if="item.svg" class="nav-svg-icon"><use :href="item.svg" /></svg>
              <font-awesome-icon v-else :icon="item.icon" />
              <span>{{ item.title }}</span>
              <span v-if="item.badge" class="badge">{{ item.badge }}</span>
            </a>
          </li>
        </ul>
      </nav>
      <!-- Page content -->
      <div class="page-content">
        <slot name="content"></slot>
      </div>
    </div>
    <div class="content-overlay" @click="collapsed = true"></div>
  </div>
</template>

<script>
import get from 'lodash/get'
import { inMobileWebView, user, settings } from '@src/lib/page-context'

export default {
  name: 'PageLayout',

  props: {
    isPopup: {
      type: Boolean,
      default: false,
    },
  },

  data() {
    return {
      collapsed: true,
      path: window.location.pathname,
      user: null,
      allowSignUp: false,
      isEnt: false,
    }
  },

  computed: {
    inMobileWebView() {
      return inMobileWebView()
    },
    hideHeader() {
      const urlParams = new URLSearchParams(window.location.search)
      return urlParams.get('hide_header') === 'true'
    },
    hideTopNav() {
      // The top bar carries the site navigation, so it's only dropped when embedded, or in the
      // mobile app (which has its own navigation) on pages with nothing of their own to show in it.
      const noPageContent = !this.$slots.topBarLeft && !this.$slots.topBarRight
      return this.hideHeader || (this.inMobileWebView && noPageContent)
    },
    mainNavItems() {
      if (!this.user) return []
      const path = this.path
      return [
        this.isEnt && !this.user.is_pro && {
          key: 'upgrade',
          href: '/ent_pub/pricing/',
          icon: 'star',
          title: `${this.$t('Upgrade to')} AI Premium`,
          primary: true,
          active: path === '/ent_pub/pricing/',
        },
        {
          key: 'printers',
          href: '/printers/',
          svg: '#svg-3d-printer',
          title: this.$t('Printers'),
          active: path.includes('/printers/'),
        },
        {
          key: 'gcodes',
          href: '/g_code_folders/cloud/',
          icon: 'fa-file-code',
          title: this.$t('G-Codes'),
          active: path.includes('/g_code_'),
        },
        {
          key: 'history',
          href: '/print_history/',
          icon: 'fa-calendar-days',
          title: this.$t('Print History'),
          active: path.includes('/print_history/') || path.includes('/prints/'),
        },
        {
          key: 'stats',
          href: '/stats/',
          icon: 'fa-chart-pie',
          title: this.$t('Statistics'),
          active: path.includes('/stats/'),
        },
        this.isEnt && {
          key: 'pricing',
          href: '/ent_pub/pricing/',
          icon: 'fa-money-check-dollar',
          title: this.$t('Pricing'),
          active: path === '/ent_pub/pricing/',
        },
      ].filter(Boolean)
    },
    userNavItems() {
      if (!this.user) return []
      return [
        {
          key: 'notifications',
          href: '/printer_events/',
          icon: 'fas fa-bell',
          title: this.$t('Notifications'),
          badge: this.hasUnseenPrinterEvents ? this.unseenPrinterEventsDisplay : null,
          active: this.path === '/printer_events/',
        },
        {
          key: 'preferences',
          href: '/user_preferences/',
          icon: 'fas fa-cog',
          title: this.$t('Preferences'),
          active: this.path.includes('/user_preferences/'),
        },
      ]
    },
    hasUnseenPrinterEvents() {
      return get(this.user, 'unseen_printer_events', 0) > 0
    },
    unseenPrinterEventsDisplay() {
      const num = get(this.user, 'unseen_printer_events', 0)
      return num > 99 ? '99+' : num
    },
  },

  created() {
    const { ACCOUNT_ALLOW_SIGN_UP, IS_ENT } = settings()
    this.allowSignUp = !!ACCOUNT_ALLOW_SIGN_UP
    this.isEnt = !!IS_ENT
    this.user = user()
  },

  mounted() {
    // Temporary solution to correctly show alerts inserted by `snippets/messages.html` from Django
    const staticAlert = document.querySelector('.message-snippet')
    if (staticAlert && this.inMobileWebView) {
      staticAlert.classList.add('is-in-mobile')
    }
    if (this.inMobileWebView) {
      document.querySelector('body').style.paddingBottom = 0
    }
  },
}
</script>

<style lang="sass">
.message-snippet
  margin-top: 50px
</style>

<style lang="sass" scoped>
// Below this width the nav links collapse into the hamburger dropdown
$nav-collapse-width: 1024px

.page-wrapper
  display: flex
  background-color: var(--color-background)
  &.is-in-mobile, &.is-in-popup
    .brand, .main-nav, .user-nav, .toggle-sidebar, .nav-dropdown, .content-overlay
      display: none !important

  &.is-in-mobile
    .content-wrapper.hide-top-nav
      .page-content
        padding: 15px 0

  &.is-in-popup
    .top-nav
      position: relative
      padding: 0 15px
    .page-content
      padding: 15px 0
      min-height: unset

.content-wrapper
  width: 100%

.top-nav
  height: 50px
  background: var(--color-surface-primary) !important
  position: fixed
  top: 0
  left: 0
  width: 100%
  padding-left: 15px
  padding-right: 32px
  z-index: 100
  box-shadow: var(--shadow-top-nav)
  justify-content: space-between
  flex-wrap: nowrap

  ::v-deep .action-panel
    .action-btn
      box-sizing: border-box
      width: 36px
      padding-left: 0
      padding-right: 0
      @media (max-width: 768px)
        display: none

.top-nav-left, .top-nav-right
  display: flex
  align-items: center
  min-width: 0
.top-nav-left
  gap: 1rem
.top-nav-right
  flex-shrink: 0
  gap: 0.5rem

.brand
  display: flex
  align-items: center
  flex-shrink: 0

.page-title
  display: flex
  align-items: center
  min-width: 0
  padding-left: 1rem
  border-left: 1px solid var(--color-divider)

.nav-svg-icon
  width: 1.2em
  height: 1.2em

.main-nav, .user-nav
  display: flex
  align-items: center
  gap: 2px
  li a
    position: relative
    display: flex
    align-items: center
    gap: 0.4rem
    color: var(--color-text-primary)
    padding: 6px 10px
    border-radius: var(--border-radius-sm)
    font-size: 0.9rem
    white-space: nowrap
    &:hover
      color: var(--color-text-primary)
      background: var(--color-hover)
      text-decoration: none
    &.primary
      color: var(--color-primary)
  li.active > a
    background: var(--color-hover)

.user-nav li a
  padding: 6px 8px
  font-size: 1rem

.badge
  border-radius: 2em
  padding: 0.25em 0.3em
  font-family: var(--default-font-family)
  font-size: .714rem
  font-weight: bold
  color: var(--color-on-danger)
  background-color: var(--color-danger)
  min-width: 1.6em
.user-nav .badge
  position: absolute
  top: -0.1em
  right: -0.2em

.toggle-sidebar
  color: var(--color-text-primary)
  display: none

.nav-dropdown
  position: fixed
  top: 50px
  right: 0
  width: 260px
  max-width: 100%
  max-height: calc(100vh - 50px)
  overflow-y: auto
  z-index: 100
  padding: 4px
  background: var(--color-surface-primary)
  border-left: 1px solid var(--color-divider)
  border-bottom: 1px solid var(--color-divider)
  box-shadow: var(--shadow-top-nav)
  li a
    display: flex
    align-items: center
    gap: 0.75rem
    padding: 10px 12px
    color: var(--color-text-primary)
    border-radius: var(--border-radius-sm)
    &:hover
      background: var(--color-hover)
      text-decoration: none
    &.primary
      color: var(--color-primary)
    svg, ::v-deep .svg-inline--fa
      width: 1.2em
    .badge
      margin-left: auto
  li.active > a
    background: var(--color-hover)

.page-content
  padding: calc(50px + var(--gap-between-blocks)) calc(var(--gap-between-blocks) - 15px) var(--gap-between-blocks)
  display: flex
  flex-direction: column
  min-height: 100vh

.content-overlay
  position: fixed
  top: 50px
  left: 0
  width: 100%
  height: 100%
  z-index: 99
  background: rgb(0,0,0,.5)
  display: none

::v-deep .search-input input
  background-color: var(--color-surface-secondary)
  border-color: var(--color-surface-secondary)

.notification-dot
  height: 50%
  top: -9px
  right: -3px
  position: absolute
  width: 50%
  color: var(--color-danger)

.content-wrapper.hide-top-nav
  .top-nav
    display: none
  .page-content
    padding-top: 30px
    min-height: 100vh

@media (min-width: $nav-collapse-width + 1)
  .nav-dropdown, .content-overlay
    display: none !important

@media (max-width: $nav-collapse-width)
  .main-nav, .user-nav
    display: none
  .toggle-sidebar
    display: block
  .page-wrapper:not(.collapsed) .content-overlay
    display: block
  .top-nav-left
    gap: 0.75rem
  .page-title
    padding-left: 0.75rem

@media (max-width: 768px)
  .top-nav
    padding-left: 15px
    padding-right: 15px

  .page-content
    padding: 15px 0
    padding-top: calc(50px + 15px)
    justify-content: start

  .content-wrapper.hide-top-nav .page-content
    padding-top: 15px

@media (max-width: 510px)
  // Leave room for the page's own title and actions
  .brand
    display: none
  .page-title
    padding-left: 0
    border-left: none

::v-deep .dropdown-menu
  .dropdown-item
    padding: 0.25rem 0.75rem
    .clickable-area
      margin: -0.25rem -0.75rem
      padding: 0.25rem 0.75rem
    i, .custom-svg-icon
      width: 20px
      margin-right: .5rem
  .b-dropdown-text
    padding-left: 0.75rem
    padding-right: 0.75rem
</style>
