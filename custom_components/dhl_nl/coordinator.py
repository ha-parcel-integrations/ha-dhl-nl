"""Coordinator for the DHL Package Tracker integration."""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .api import DhlApiClient, DhlApiError, DhlAuthError
from .const import (
    CONF_INCLUDE_HISTORY,
    CONF_REFRESH_INTERVAL,
    DEFAULT_INCLUDE_HISTORY,
    DEFAULT_REFRESH_INTERVAL,
    DOMAIN,
    ParcelStatus,
)

_LOGGER = logging.getLogger(__name__)

from .parcels import (
    _apply_delivered_filter,
    build_history,
    filter_active_parcels,
    filter_active_returns,
    filter_active_sent_shipments,
    filter_delivered_parcels,
    filter_delivered_returns,
    filter_delivered_sent_shipments,
    normalize_parcel,
    sort_parcels_by_ts,
)

def _refresh_interval(entry: ConfigEntry) -> timedelta:
    """Return the configured refresh interval as a ``timedelta``."""
    minutes = int(entry.options.get(CONF_REFRESH_INTERVAL, DEFAULT_REFRESH_INTERVAL))
    return timedelta(minutes=minutes)


class DhlCoordinator(DataUpdateCoordinator[list[dict]]):
    """Coordinator that polls the DHL parcels API on a fixed schedule."""

    def __init__(self, hass: HomeAssistant, client: DhlApiClient, entry: ConfigEntry) -> None:
        """Initialise the coordinator.

        Args:
            hass: The Home Assistant instance.
            client: An authenticated :class:`DhlApiClient` instance.
            entry: The config entry, used to read options for the delivered
                filter and the configured refresh interval.
        """
        super().__init__(
            hass,
            _LOGGER,
            config_entry=entry,
            name=DOMAIN,
            update_interval=_refresh_interval(entry),
        )
        self._client = client
        self.delivered: list[dict] = []
        # Return parcels — sourced from the same parcels list as incoming,
        # filtered on isReturn instead of excluded. ``returning`` mirrors
        # ``data`` (active, sorted by planned_from); ``delivered_outgoing``
        # mirrors ``delivered`` (completed, sorted by delivered_at desc).
        self.returning: list[dict] = []
        self.delivered_outgoing: list[dict] = []
        # barcode -> last seen ParcelStatus. ``None`` on the first refresh so
        # we can suppress events for parcels that already existed when the
        # integration started (we do not know their previous state).
        self._known_state: dict[str, ParcelStatus] | None = None
        # barcode -> last seen (planned_from, planned_to) tuple. Mirrors
        # ``_known_state`` for delivery-time-change detection.
        self._known_delivery_times: (
            dict[str, tuple[str | None, str | None]] | None
        ) = None
        # barcode -> last seen ParcelStatus for *outgoing* parcels (returns +
        # sent), tracked across the active (``returning``) and delivered
        # (``delivered_outgoing``) buckets so a status change or a
        # transition-to-delivered fires an outgoing event. ``None`` on the
        # first refresh for the same suppression reason as ``_known_state``.
        self._known_outgoing_state: dict[str, ParcelStatus] | None = None
        # barcode -> {"history": [...], "_raw_status": str}. The track-trace
        # call is an extra HTTP request per parcel, so we only make it when
        # the history option is on, and only refetch when a parcel's raw
        # status changes (history only grows on a status change). The cache
        # lives for the integration's lifetime (resets on HA restart).
        self._history_cache: dict[str, dict] = {}
        # Cached device id for this account, attached to every fired event so
        # device-trigger automations can filter to a specific DHL account.
        # ``None`` until the device exists (i.e. the sensors are set up).
        self._cached_device_id: str | None = None
        # Timestamp of the last successful poll, surfaced by a diagnostic
        # sensor so users can alert on a silently-stale integration (the
        # count sensors only change when a value changes, not every poll).
        self.last_success_time: datetime | None = None

    def _device_id(self) -> str | None:
        """Resolve (and cache) this account's device id for event payloads.

        Looked up from the device registry by config entry. Stays ``None``
        until the device has been registered (the sensors create it on first
        setup), which is harmless because events are suppressed on the very
        first refresh anyway.
        """
        if self._cached_device_id is not None:
            return self._cached_device_id
        registry = dr.async_get(self.hass)
        device = next(
            iter(dr.async_entries_for_config_entry(registry, self.config_entry.entry_id)),
            None,
        )
        if device is not None:
            self._cached_device_id = device.id
        return self._cached_device_id

    @property
    def _include_history(self) -> bool:
        """Whether the opt-in per-parcel history option is enabled."""
        return bool(
            self.config_entry.options.get(
                CONF_INCLUDE_HISTORY, DEFAULT_INCLUDE_HISTORY
            )
        )

    def _normalize(self, parcel: dict) -> dict:
        """Normalize a raw parcel, attaching any cached history timeline."""
        barcode = parcel.get("barcode") or ""
        history = (self._history_cache.get(barcode) or {}).get("history")
        return normalize_parcel(parcel, history=history)

    async def _async_update_data(self) -> list[dict]:
        try:
            raw = await self._client.async_get_parcels()
        except DhlAuthError as err:
            raise ConfigEntryAuthFailed("DHL authentication failed") from err
        except DhlApiError as err:
            raise UpdateFailed(f"DHL error: {err}") from err
        # aiohttp.ClientError is wrapped automatically by DataUpdateCoordinator.

        active = filter_active_parcels(raw)
        delivered = self._apply_delivered_filter(filter_delivered_parcels(raw))
        returning = filter_active_returns(raw)
        delivered_returns = self._apply_delivered_filter(filter_delivered_returns(raw))
        _LOGGER.debug(
            "DHL parcels fetched: %d total, %d active, %d delivered, "
            "%d returning, %d returned",
            len(raw), len(active), len(delivered), len(returning), len(delivered_returns),
        )
        # Fetch the track-trace timeline for active + delivered parcels when
        # the option is on, before normalizing so the history is attached.
        # Returns are excluded — track-trace is a receiver-role endpoint.
        await self._enrich_history(active + delivered)

        self.delivered = sort_parcels_by_ts(
            [self._normalize(p) for p in delivered],
            "delivered_at",
            descending=True,
        )
        normalized_active = sort_parcels_by_ts(
            [self._normalize(p) for p in active], "planned_from"
        )
        self.returning = sort_parcels_by_ts(
            [self._normalize(p) for p in returning], "planned_from"
        )
        self.delivered_outgoing = sort_parcels_by_ts(
            [self._normalize(p) for p in delivered_returns],
            "delivered_at",
            descending=True,
        )

        # Incoming = active + delivered, combined so the transition to
        # delivered is visible in one set (mirrors the outgoing pattern).
        incoming = normalized_active + self.delivered
        self._fire_change_events(incoming)

        # Outgoing = returns (active) + delivered returns, combined so a
        # transition from in-transit to delivered is visible in one set.
        outgoing = self.returning + self.delivered_outgoing
        self._fire_outgoing_change_events(outgoing)

        self._known_state = {
            p["barcode"]: p["status"]
            for p in incoming
            if p.get("barcode")
        }
        self._known_delivery_times = {
            p["barcode"]: (p.get("planned_from"), p.get("planned_to"))
            for p in incoming
            if p.get("barcode")
        }
        self._known_outgoing_state = {
            p["barcode"]: p["status"]
            for p in outgoing
            if p.get("barcode")
        }

        self.last_success_time = datetime.now(timezone.utc)
        return normalized_active

    def _fire_change_events(self, parcels: list[dict]) -> None:
        """Fire events for newly-registered parcels and parcel transitions.

        Silent on the very first refresh — we cannot reliably know which
        parcels are "new" to the user vs. "already there before HA started".
        From the second refresh onward, every not-yet-delivered parcel that
        was not present before yields one ``dhl_nl_parcel_registered`` event,
        every parcel whose normalized status transitions **to** ``DELIVERED``
        yields one ``dhl_nl_parcel_delivered`` event, every other status
        change yields one ``dhl_nl_parcel_status_changed`` event, and every
        parcel whose ``planned_from`` or ``planned_to`` changed to a non-null
        value yields one ``dhl_nl_parcel_delivery_time_changed`` event.
        ``delivered`` takes precedence over ``status_changed`` for the
        terminal hop, so it fires exactly one (dedicated) event, not both. A
        parcel that is already delivered the first time it is seen never
        fires — mirroring the outgoing events.
        """
        if self._known_state is None:
            return

        known_times = self._known_delivery_times or {}
        device_id = self._device_id()

        for parcel in parcels:
            barcode = parcel.get("barcode")
            if not barcode:
                continue
            new_status = parcel["status"]
            if barcode not in self._known_state:
                if new_status != ParcelStatus.DELIVERED:
                    self.hass.bus.async_fire(
                        f"{DOMAIN}_parcel_registered",
                        {**parcel, "device_id": device_id},
                    )
                continue

            if self._known_state[barcode] != new_status:
                if new_status == ParcelStatus.DELIVERED:
                    self.hass.bus.async_fire(
                        f"{DOMAIN}_parcel_delivered",
                        {**parcel, "device_id": device_id},
                    )
                else:
                    self.hass.bus.async_fire(
                        f"{DOMAIN}_parcel_status_changed",
                        {
                            **parcel,
                            "device_id": device_id,
                            "old_status": self._known_state[barcode],
                            "new_status": new_status,
                        },
                    )

            old_from, old_to = known_times.get(barcode, (None, None))
            new_from = parcel.get("planned_from")
            new_to = parcel.get("planned_to")
            # Fire only when at least one of the two ends up with a real
            # (non-null) value AND that value differs from the last-known
            # one. value -> null transitions are intentionally silent —
            # they mean the carrier dropped the ETA, which is not what
            # users want to be paged about.
            from_changed = new_from is not None and new_from != old_from
            to_changed = new_to is not None and new_to != old_to
            if from_changed or to_changed:
                self.hass.bus.async_fire(
                    f"{DOMAIN}_parcel_delivery_time_changed",
                    {
                        **parcel,
                        "device_id": device_id,
                        "old_planned_from": old_from,
                        "new_planned_from": new_from,
                        "old_planned_to": old_to,
                        "new_planned_to": new_to,
                    },
                )

    def _fire_outgoing_change_events(self, parcels: list[dict]) -> None:
        """Fire status/delivered events for outgoing parcels (returns + sent).

        Silent on the very first refresh (``_known_outgoing_state is None``),
        matching ``_fire_change_events``. From the second refresh onward, every
        outgoing parcel whose normalized status transitions **to**
        ``DELIVERED`` yields one ``dhl_nl_outgoing_parcel_delivered`` event, and
        every other status change yields one
        ``dhl_nl_outgoing_parcel_status_changed`` event. ``delivered`` takes
        precedence over ``status_changed`` for that final transition, so the
        terminal hop fires exactly one (dedicated) event, not both. A parcel
        that is already delivered the first time it is seen never fires (its
        status did not change). There is no outgoing ``registered`` or
        ``delivery_time_changed`` event — those are intentionally out of scope.
        """
        if self._known_outgoing_state is None:
            return

        device_id = self._device_id()

        for parcel in parcels:
            barcode = parcel.get("barcode")
            if not barcode or barcode not in self._known_outgoing_state:
                continue
            old_status = self._known_outgoing_state[barcode]
            new_status = parcel["status"]
            if new_status == old_status:
                continue

            if new_status == ParcelStatus.DELIVERED:
                self.hass.bus.async_fire(
                    f"{DOMAIN}_outgoing_parcel_delivered",
                    {**parcel, "device_id": device_id},
                )
            else:
                self.hass.bus.async_fire(
                    f"{DOMAIN}_outgoing_parcel_status_changed",
                    {
                        **parcel,
                        "device_id": device_id,
                        "old_status": old_status,
                        "new_status": new_status,
                    },
                )

    async def _enrich_history(self, parcels: list[dict]) -> None:
        """Populate ``self._history_cache`` from the track-trace endpoint.

        Opt-in only. For each parcel we call track-trace on first sight and
        again only when its raw ``status`` changes (history grows on status
        changes), mirroring DPD's detail-cache cost control. Best-effort: a
        ``None`` response leaves any prior history in place and never breaks
        the poll. The query needs the parcel's ``parcelId`` (uuid) and the
        receiver's postcode, both from the list endpoint.
        """
        if not self._include_history:
            return
        for parcel in parcels:
            barcode = parcel.get("barcode")
            if not barcode:
                continue
            raw_status = parcel.get("status") or ""
            cached = self._history_cache.get(barcode)
            if cached is not None and cached.get("_raw_status") == raw_status:
                continue
            postal = (
                ((parcel.get("receiver") or {}).get("address") or {}).get(
                    "postalCode"
                )
                or ""
            ).replace(" ", "")
            parcel_id = parcel.get("parcelId")
            if not postal or not parcel_id:
                continue
            track_trace = await self._client.async_get_track_trace(
                barcode, postal, parcel_id
            )
            if track_trace is None:
                continue
            self._history_cache[barcode] = {
                "history": build_history(track_trace),
                "_raw_status": raw_status,
            }

    def _apply_delivered_filter(self, parcels: list[dict]) -> list[dict]:
        """Apply the configured filter to the delivered parcels list."""
        return _apply_delivered_filter(parcels, self.config_entry)


class DhlSentShipmentsCoordinator(DataUpdateCoordinator[list[dict]]):
    """Coordinator that polls the DHL sent shipments API on a fixed schedule."""

    def __init__(self, hass: HomeAssistant, client: DhlApiClient, entry: ConfigEntry) -> None:
        """Initialise the coordinator.

        Args:
            hass: The Home Assistant instance.
            client: An authenticated :class:`DhlApiClient` instance.
            entry: The config entry, used to read the configured refresh interval.
        """
        super().__init__(
            hass,
            _LOGGER,
            config_entry=entry,
            name=f"{DOMAIN}_sent",
            update_interval=_refresh_interval(entry),
        )
        self._client = client
        # Delivered own-sender shipments. In practice this stays empty for
        # almost every account (see filter_active_returns docstring), but is
        # tracked for parity so the sensor layer can merge it with delivered
        # returns under a single "delivered outgoing" sensor without special
        # casing which data source actually has content.
        self.delivered: list[dict] = []

    async def _async_update_data(self) -> list[dict]:
        try:
            raw = await self._client.async_get_sent_shipments()
        except DhlAuthError as err:
            raise ConfigEntryAuthFailed("DHL authentication failed") from err
        except DhlApiError as err:
            raise UpdateFailed(f"DHL error (sent): {err}") from err
        # aiohttp.ClientError is wrapped automatically by DataUpdateCoordinator.

        active = filter_active_sent_shipments(raw)
        delivered = _apply_delivered_filter(
            filter_delivered_sent_shipments(raw), self.config_entry
        )
        _LOGGER.debug(
            "DHL sent shipments fetched: %d total, %d active, %d delivered",
            len(raw), len(active), len(delivered),
        )
        self.delivered = sort_parcels_by_ts(
            [normalize_parcel(s) for s in delivered],
            "delivered_at",
            descending=True,
        )
        return sort_parcels_by_ts(
            [normalize_parcel(s) for s in active], "planned_from"
        )
