"""
gui/handlers.py — Bot start/stop, all event handlers and toggle callbacks
"""

from PyQt5.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QLabel, QPushButton, QGroupBox, QTextEdit, QFrame,
    QTabWidget, QTableWidget, QTableWidgetItem, QHeaderView,
    QDoubleSpinBox, QSpinBox, QComboBox, QSplitter, QSizePolicy,
    QProgressBar, QCheckBox, QScrollArea, QLineEdit,
    QSystemTrayIcon, QMenu, QAction, QGridLayout,
)
from PyQt5.QtCore import Qt, QTimer, pyqtSignal, QObject
from PyQt5.QtGui import QColor, QFont, QPainter, QPainterPath, QLinearGradient, QPen
from .theme import C, SS
from .widgets import Sig, Sparkline, _stat_card, _vline, _hline
from .shared_imports import *


class HandlersMixin:
    def _start(self):
        sym = self.sym_combo.currentText().strip() or WATCH_SYMBOL
        lot = self.spin_lot.value()
        follow = self.chk_follow.isChecked()
        soft_lot_mode = {0: 1, 1: 2, 2: 3}.get(
            self.lot_mode_combo.currentIndex(), 1)
        self.sparkline.clear()

        cfg.LOT_SIZE = lot
        cfg.SOFT_LOT_MODE = soft_lot_mode
        cfg.TP_RR_RATIO = 0.0
        cfg.BALANCE_TP_RATIO = 1.0 + self.spin_balance_tp.value() / 100.0

        self.lbl_sym_hdr.setText(sym)

        self._worker = WatcherThread(
            symbol=sym,
            lot_size=lot,
            follow_enabled=follow,
            resume_enabled=self.chk_resume.isChecked(),
            risk_free_enabled=self.chk_risk_free.isChecked(),
            loss_free_enabled=self.chk_loss_free.isChecked(),
            soft_lot_mode=soft_lot_mode,
            tp_free=self.chk_tp_free.isChecked(),
            entry_filter_ob_fvg=self.chk_entry_filter.isChecked(),
            partial_exit_r3=self.chk_partial_exit.isChecked(),
            trailing_sl=self.chk_trailing.isChecked(),
        )
        self._worker.sig.on_log(lambda m, l: self._sig.log_line.emit(m, l))
        self._worker.sig.on_status(lambda s:    self._sig.status.emit(s))
        self._worker.sig.on_state(lambda s:    self._sig.state.emit(s))
        self._worker.sig.on_candle(lambda c:    self._sig.candle.emit(c))
        # ← NEW: wire balance TP signal so GUI can stop all watchers cleanly
        self._worker.sig.on_stop(lambda:      self._sig.balance_tp.emit())
        self._worker.start()

        self.btn_start.setEnabled(False)
        self.btn_stop.setEnabled(True)
        # Start trade DB session
        sym_for_db = self.sym_combo.currentText().strip()
        try:
            import MetaTrader5 as _mt5
            info = _mt5.account_info()
            bal = info.balance if info else 0.0
        except Exception:
            bal = 0.0
        trade_db.start_session(sym_for_db, bal,
                               lot_mode=self.lot_mode_combo.currentIndex()+1)
        self._on_status("🟡  Starting…")

        # Start FVG watcher if enabled
        if self.chk_fvg.isChecked():
            self._fvg_worker = FVGWatcher(
                symbol=sym,
                min_gap_pips=self.spin_fvg_gap.value(),
                lookback=self.spin_fvg_lookback.value(),
                max_draw=self.spin_fvg_max.value(),
                scan_interval=5.0,
                log_fn=lambda m, l="INFO": self._sig.log_line.emit(m, l),
            )
            self._fvg_worker.start()

        # Start Rectangle Suggestions if enabled (visualization only —
        # never places an order, see core/rect_suggest_detector.py)
        if self.chk_rectsug.isChecked():
            self._rect_suggest_worker = RectSuggestWatcher(
                symbol=sym,
                min_bars=self.spin_rectsug_bars.value(),
                max_range_atr_mult=self.spin_rectsug_range.value(),
                max_draw=self.spin_rectsug_max.value(),
                scan_interval=5.0,
                log_fn=lambda m, l="INFO": self._sig.log_line.emit(m, l),
            )
            self._rect_suggest_worker.start()

        # Start OB watcher if enabled
        if self.chk_ob.isChecked():
            self._ob_worker = OBWatcher(
                symbol=sym,
                min_impulse_pips=self.spin_ob_impulse.value(),
                lookback=self.spin_ob_lookback.value(),
                swing_lookback=self.spin_ob_swing.value(),
                max_draw=self.spin_ob_max.value(),
                scan_interval=5.0,
                log_fn=lambda m, l="INFO": self._sig.log_line.emit(m, l),
            )
            self._ob_worker.start()

        # Start MTF FVG watcher if enabled
        if self.chk_mtf.isChecked():
            self._start_mtf(sym)

        # Start AMD watcher if enabled
        if self.chk_amd.isChecked():
            self._start_amd(sym)

        # Start ICT Bias watcher if enabled
        if self.chk_bias.isChecked():
            self._bias_worker = BiasWatcher(
                symbol=sym,
                lookback=self.spin_bias_lookback.value(),
                scan_interval=self.spin_bias_interval.value(),
                log_fn=lambda m, l="INFO": self._sig.log_line.emit(m, l),
                on_results=lambda r: self._sig.bias_update.emit(r),
            )
            self._bias_worker.start()

        # Start Confluence watcher if enabled (requires both OB and FVG)
        if self.chk_confluence.isChecked():
            if self._ob_worker and self._fvg_worker:
                self._start_confluence(sym)
            else:
                self.chk_confluence.blockSignals(True)
                self.chk_confluence.setChecked(False)
                self.chk_confluence.blockSignals(False)
                self._on_log(
                    f"{datetime.now().strftime('%H:%M:%S')}  "
                    f"⚠️  Confluence disabled: enable OB and FVG first, then check Confluence",
                    "WARN"
                )

    def _stop(self):
        """Stop all watchers cleanly in the correct order."""
        # Confluence first (re-enables OB/FVG draw_on_chart)
        self._stop_confluence()
        if self._amd_worker:
            self._amd_worker.stop()
            self._amd_worker = None
        if self._bias_worker:
            self._bias_worker.stop()
            self._bias_worker = None
        if self._mtf_fvg_worker:
            self._mtf_fvg_worker.stop()
            self._mtf_fvg_worker = None
        # FVG and OB before MT5 shutdown
        if self._fvg_worker:
            self._fvg_worker.stop()
            self._fvg_worker = None
        if self._rect_suggest_worker:
            self._rect_suggest_worker.stop()
            self._rect_suggest_worker = None
        if self._ob_worker:
            self._ob_worker.stop()
            self._ob_worker = None
        # Main watcher last — calls mt5.shutdown() at end of its run()
        if self._worker:
            self._worker.stop()
            self._worker = None
        self.btn_start.setEnabled(True)
        self.btn_stop.setEnabled(False)
        try:
            import MetaTrader5 as _mt5
            info = _mt5.account_info()
            bal = info.balance if info else 0.0
        except Exception:
            bal = 0.0
        trade_db.end_session(bal)
        self._on_status("⚫  Stopped")

    # ← NEW: called on Qt main thread when balance TP fires
    def _on_balance_tp_reached(self):
        """Stop all watchers cleanly after balance TP. Called on Qt main thread."""
        self._on_log(
            f"{datetime.now().strftime('%H:%M:%S')}  "
            f"🎯  Balance TP reached — stopping all watchers", "NEW"
        )
        # Stop in correct order: confluence → FVG/OB/AMD/MTF → watcher exits naturally
        self._stop_confluence()
        if self._mtf_fvg_worker:
            self._mtf_fvg_worker.stop()
            self._mtf_fvg_worker = None
        if self._amd_worker:
            self._amd_worker.stop()
            self._amd_worker = None
        if self._bias_worker:
            self._bias_worker.stop()
            self._bias_worker = None
        if self._mtf_fvg_worker:
            self._mtf_fvg_worker.stop()
            self._mtf_fvg_worker = None
        if self._fvg_worker:
            self._fvg_worker.stop()
            self._fvg_worker = None
        if self._rect_suggest_worker:
            self._rect_suggest_worker.stop()
            self._rect_suggest_worker = None
        if self._ob_worker:
            self._ob_worker.stop()
            self._ob_worker = None
        # Don't call _worker.stop() — it already set its own stop event.
        # Just clear the reference; mt5.shutdown() runs at end of watcher.run().
        self._worker = None
        self.btn_start.setEnabled(True)
        self.btn_stop.setEnabled(False)
        self._on_status("🎯  Balance TP — session complete")

    def _start_confluence(self, sym: str = None):
        if sym is None:
            sym = self.sym_combo.currentText().strip() or WATCH_SYMBOL
        if not self._ob_worker or not self._fvg_worker:
            self._on_log(
                f"{datetime.now().strftime('%H:%M:%S')}  "
                f"⚠️  Confluence needs both OB and FVG enabled — "
                f"enable them first, then activate Confluence", "WARN"
            )
            self.chk_confluence.blockSignals(True)
            self.chk_confluence.setChecked(False)
            self.chk_confluence.blockSignals(False)
            return
        if self._confluence_worker:
            return

        self._confluence_worker = ConfluenceWatcher(
            symbol=sym,
            ob_watcher=self._ob_worker,
            fvg_watcher=self._fvg_worker,
            max_candles_after=self.spin_conf_window.value(),
            require_direction=self.chk_conf_direction.isChecked(),
            scan_interval=5.0,
            max_draw=self.spin_conf_max.value(),
            log_fn=lambda m, l="INFO": self._sig.log_line.emit(m, l),
        )
        self._confluence_worker.start()

    def _stop_confluence(self):
        if self._confluence_worker:
            self._confluence_worker.stop()
            self._confluence_worker = None

    def _cancel_all(self):
        sym = self.sym_combo.currentText().strip() or WATCH_SYMBOL
        try:
            orders = mt5.orders_get(symbol=sym) or []
            cancelled = 0
            for o in orders:
                if o.magic == MAGIC_NUMBER:
                    res = mt5.order_send(
                        {"action": mt5.TRADE_ACTION_REMOVE, "order": o.ticket})
                    if res and res.retcode == mt5.TRADE_RETCODE_DONE:
                        cancelled += 1
            ts = datetime.now().strftime("%H:%M:%S")
            self._on_log(f"{ts}  🗑️  Cancelled {cancelled} bot orders", "WARN")
        except Exception as e:
            self._on_log(f"Cancel error: {e}", "ERROR")

    # ── Toggle Handlers (live enable/disable) ─────────────────────

    def _on_rectsug_toggled(self, state):
        if not self._worker:
            return
        sym = self.sym_combo.currentText().strip() or WATCH_SYMBOL
        if state == Qt.Checked:
            if not self._rect_suggest_worker:
                self._rect_suggest_worker = RectSuggestWatcher(
                    symbol=sym,
                    min_bars=self.spin_rectsug_bars.value(),
                    max_range_atr_mult=self.spin_rectsug_range.value(),
                    max_draw=self.spin_rectsug_max.value(),
                    scan_interval=5.0,
                    log_fn=lambda m, l="INFO": self._sig.log_line.emit(m, l),
                )
                self._rect_suggest_worker.start()
        else:
            if self._rect_suggest_worker:
                self._rect_suggest_worker.stop()
                self._rect_suggest_worker = None
            self.lbl_rectsug_count.setText("Suggestions: — (disabled)")

    def _on_rectsug_settings_changed(self):
        if self._rect_suggest_worker:
            self._rect_suggest_worker.update_settings(
                min_bars=self.spin_rectsug_bars.value(),
                max_range_atr_mult=self.spin_rectsug_range.value(),
                max_draw=self.spin_rectsug_max.value(),
            )

    def _on_fvg_toggled(self, state):
        if not self._worker:
            return
        sym = self.sym_combo.currentText().strip() or WATCH_SYMBOL
        if state == Qt.Checked:
            if not self._fvg_worker:
                self._fvg_worker = FVGWatcher(
                    symbol=sym,
                    min_gap_pips=self.spin_fvg_gap.value(),
                    lookback=self.spin_fvg_lookback.value(),
                    max_draw=self.spin_fvg_max.value(),
                    scan_interval=5.0,
                    log_fn=lambda m, l="INFO": self._sig.log_line.emit(m, l),
                )
                self._fvg_worker.start()
        else:
            if self._confluence_worker:
                self._stop_confluence()
                self.chk_confluence.blockSignals(True)
                self.chk_confluence.setChecked(False)
                self.chk_confluence.blockSignals(False)
                self.lbl_conf_count.setText("Confluence: — (FVG disabled)")
            if self._fvg_worker:
                self._fvg_worker.stop()
                self._fvg_worker = None
            self.lbl_fvg_count.setText("FVGs: — (disabled)")

    def _on_ob_toggled(self, state):
        if not self._worker:
            return
        sym = self.sym_combo.currentText().strip() or WATCH_SYMBOL
        if state == Qt.Checked:
            if not self._ob_worker:
                self._ob_worker = OBWatcher(
                    symbol=sym,
                    min_impulse_pips=self.spin_ob_impulse.value(),
                    lookback=self.spin_ob_lookback.value(),
                    swing_lookback=self.spin_ob_swing.value(),
                    max_draw=self.spin_ob_max.value(),
                    scan_interval=5.0,
                    log_fn=lambda m, l="INFO": self._sig.log_line.emit(m, l),
                )
                self._ob_worker.start()
        else:
            if self._confluence_worker:
                self._stop_confluence()
                self.chk_confluence.blockSignals(True)
                self.chk_confluence.setChecked(False)
                self.chk_confluence.blockSignals(False)
                self.lbl_conf_count.setText("Confluence: — (OB disabled)")
            if self._ob_worker:
                self._ob_worker.stop()
                self._ob_worker = None
            self.lbl_ob_count.setText("OBs: — (disabled)")

    def _on_confluence_toggled(self, state):
        if not self._worker:
            return
        if state == Qt.Checked:
            if not self._ob_worker or not self._fvg_worker:
                self._on_log(
                    f"{datetime.now().strftime('%H:%M:%S')}  "
                    f"⚠️  Enable OB and FVG first, then activate Confluence", "WARN"
                )
                self.chk_confluence.blockSignals(True)
                self.chk_confluence.setChecked(False)
                self.chk_confluence.blockSignals(False)
                return
            self._start_confluence()
        else:
            self._stop_confluence()
            self.lbl_conf_count.setText("Confluence: — (disabled)")

    # ── Signal Handlers ───────────────────────────────────────────

    def _on_log(self, msg: str, level: str = "INFO"):
        colors = {"ERROR": C['red'], "WARN": C['orange'], "NEW": C['green']}
        color = colors.get(level, C['txt'])
        self.log_view.append(f'<span style="color:{color};">{msg}</span>')
        sb = self.log_view.verticalScrollBar()
        sb.setValue(sb.maximum())
        self.lbl_sb.setText(msg[:100])
        # Fire notifications
        try:
            notif_manager.check(msg, level)
        except Exception:
            pass
        # Record trade events to DB
        try:
            self._record_trade_event(msg, level)
        except Exception:
            pass

    def _on_status(self, msg: str):
        self.lbl_status.setText(msg)

    def _on_state(self, states: list):
        counts = {SourceState.IDLE: 0, SourceState.PENDING: 0,
                  SourceState.ACTIVE: 0, SourceState.EXHAUSTED: 0}
        for s in states:
            counts[s["state"]] = counts.get(s["state"], 0) + 1

        self._src_cards["total"].setText(str(len(states)))
        self._src_cards["idle"].setText(str(counts[SourceState.IDLE]))
        self._src_cards["pending"].setText(str(counts[SourceState.PENDING]))
        self._src_cards["active"].setText(str(counts[SourceState.ACTIVE]))
        self._src_cards["exhausted"].setText(
            str(counts[SourceState.EXHAUSTED]))

        self.src_table.setRowCount(len(states))
        active_lines = []
        for r, s in enumerate(states):
            st = s["state"]
            rnd = s["round"]
            buy_lot = s.get("buy_lot",  s.get("lot", 0.0))
            sell_lot = s.get("sell_lot", s.get("lot", 0.0))

            state_color = {
                SourceState.IDLE:      C['txt2'],
                SourceState.PENDING:   C['orange'],
                SourceState.ACTIVE:    C['green'],
                SourceState.EXHAUSTED: C['red'],
            }.get(st, C['txt'])

            vals = [
                (s["name"][:30],            C['txt']),
                (f"{s.get('rect_bottom', 0):.5f}-{s.get('rect_top', 0):.5f}",
                 C['cyan']),
                (st,                        state_color),
                (str(rnd) if rnd else "—",  C['gold']),
                (str(s.get("touch", 0)),    C['orange']),
                (f"{buy_lot:.2f}",          C['green']),
                (f"{sell_lot:.2f}",         C['red']),
            ]
            for c, (v, clr) in enumerate(vals):
                it = QTableWidgetItem(v)
                it.setForeground(QColor(clr))
                self.src_table.setItem(r, c, it)

            if st in (SourceState.PENDING, SourceState.ACTIVE):
                active_lines.append(
                    f"📌 {s['name'][:14]} R{rnd} | BUY {buy_lot:.2f} SELL {sell_lot:.2f}")

        self.lbl_sequences.setText(
            "\n".join(active_lines) if active_lines else "—  No active sequences"
        )

    def _on_candle(self, candle: dict):
        self._last_candle = candle
        h = candle.get("CANDLE_H", 0.0)
        l = candle.get("CANDLE_L", 0.0)
        c = candle.get("CANDLE_C", 0.0)
        if h:
            self.lbl_candle.setText(
                f"Candle  H:{h:.5f}  L:{l:.5f}  C:{c:.5f}")
        bid = candle.get("BID", 0.0)
        if bid:
            self.lbl_ea_status.setText(f"EA: ✅  bid={bid:.5f}")
            self.lbl_ea_status.setStyleSheet(
                f"color:{C['green']};font-size:10px;")

    def _refresh_price(self):
        sym = self.sym_combo.currentText().strip() or WATCH_SYMBOL
        try:
            tick = mt5.symbol_info_tick(sym)
            if tick:
                self.lbl_price.setText(f"{sym}  {tick.bid:.5f}")
        except Exception:
            pass

    def _refresh_orders(self):
        sym = self.sym_combo.currentText().strip() or WATCH_SYMBOL
        try:
            orders = mt5.orders_get(symbol=sym) or []
            bot_ord = [o for o in orders if o.magic == MAGIC_NUMBER]
            self.tbl_pending.setRowCount(len(bot_ord))
            for r, o in enumerate(bot_ord):
                is_buy = o.type == 2
                clr = QColor(C['green'] if is_buy else C['red'])
                for c, v in enumerate([str(o.ticket),
                                       "BUY-STOP" if is_buy else "SELL-STOP",
                                       f"{o.price_open:.5f}",
                                       f"{o.sl:.5f}",
                                       f"{o.volume_current:.2f}",
                                       f"{o.tp:.5f}"]):
                    it = QTableWidgetItem(v)
                    it.setForeground(clr)
                    self.tbl_pending.setItem(r, c, it)

            positions = mt5.positions_get(symbol=sym) or []
            bot_pos = [p for p in positions if p.magic == MAGIC_NUMBER]
            self.tbl_positions.setRowCount(len(bot_pos))
            total_pnl = 0.0
            buys = sells = 0
            base_lot_now = self.spin_lot.value()
            full_pct_now = self.spin_balance_tp.value()  # e.g. 10.0
            for r, p in enumerate(bot_pos):
                is_buy = p.type == 0
                clr = QColor(C['green'] if is_buy else C['red'])
                pnl_c = QColor(C['green'] if p.profit >= 0 else C['red'])
                total_pnl += p.profit
                if is_buy:
                    buys += 1
                else:
                    sells += 1

                # Live TP% this position is actually targeting right
                # now — ramps from 1% at base_lot, doubling with lot
                # size, capped at the GUI's Balance TP% setting. Must
                # match the formula in position_monitor.py exactly.
                if base_lot_now > 0:
                    lot_ratio = p.volume / base_lot_now
                    tp_pct_now = min(1.0 * lot_ratio, full_pct_now)
                else:
                    tp_pct_now = 0.0

                vals = [str(p.ticket), "BUY" if is_buy else "SELL",
                        f"{p.price_open:.5f}", f"{p.sl:.5f}",
                        f"{p.tp:.5f}", f"{tp_pct_now:.1f}%",
                        f"{p.volume:.2f}", f"{p.profit:+.2f}"]
                cols = [clr, clr, clr, clr, clr, clr, clr, pnl_c]
                for c, (v, co) in enumerate(zip(vals, cols)):
                    it = QTableWidgetItem(v)
                    it.setForeground(co)
                    self.tbl_positions.setItem(r, c, it)

            self._ord_cards["pending"].setText(str(len(bot_ord)))
            self._ord_cards["buy_pos"].setText(str(buys))
            self._ord_cards["sell_pos"].setText(str(sells))
            pnl_color = C['green'] if total_pnl >= 0 else C['red']
            self._ord_cards["total_pnl"].setText(f"{total_pnl:+.2f}")
            self._ord_cards["total_pnl"].setStyleSheet(
                f"color:{pnl_color};font-size:15px;font-weight:bold;font-family:Consolas;")

            acct = mt5.account_info()
            if acct:
                pct = self.spin_balance_tp.value()
                start_bal = acct.balance
                try:
                    import json as _json
                    import os as _os
                    _f = f"start_balance_{sym}.json"
                    if _os.path.exists(_f):
                        saved = _json.load(open(_f))
                        start_bal = saved.get("start_balance", acct.balance)
                except Exception:
                    pass
                target = start_bal * (1.0 + pct / 100.0)
                profit = acct.balance - start_bal
                self.lbl_balance.setText(
                    f"<span style='color:{C['gold']};'>{acct.balance:,.2f}</span>"
                )
                self.lbl_balance.setToolTip(f"Net change: {profit:+.2f}")
                self.lbl_balance_target.setText(f"{target:,.2f}")
                self.lbl_balance_target.setToolTip(
                    f"Start: {start_bal:.2f}  (+{pct:.0f}% target)")

                # ── Net profit (realized only — balance vs session start) ──
                net_color = C['green'] if profit >= 0 else C['red']
                self.lbl_net_profit.setText(
                    f"<span style='color:{net_color};'>{profit:+,.2f}</span>"
                )

                # ── Total PnL = realized + currently-open floating PnL.
                # acct.equity already = balance + floating PnL, so
                # equity - start_bal gives the true all-in PnL figure
                # without double-counting the open positions' profit.
                total_pnl_all = acct.equity - start_bal
                tpnl_color = C['green'] if total_pnl_all >= 0 else C['red']
                self.lbl_total_pnl_all.setText(
                    f"<span style='color:{tpnl_color};'>{total_pnl_all:+,.2f}</span>"
                )

                self.sparkline.add_value(acct.equity)

                # ── Drawdown % — how far below session-start balance the
                # account currently sits (equity-based, so it reflects
                # open losing positions too, not just closed ones). This
                # is the same basis HARD_STOP_LOSS_RATIO checks against,
                # so this number tells you how close to the kill switch
                # you are in real time.
                if start_bal > 0:
                    drawdown_pct = (start_bal - acct.equity) / \
                        start_bal * 100.0
                else:
                    drawdown_pct = 0.0
                try:
                    import config as _cfg
                    hard_stop_pct = (1.0 - getattr(
                        _cfg, "HARD_STOP_LOSS_RATIO", 0.50)) * 100.0
                except Exception:
                    hard_stop_pct = 50.0
                if drawdown_pct > 0:
                    dd_color = C['red'] if drawdown_pct >= hard_stop_pct * \
                        0.75 else C['orange']
                    self.lbl_loss_pct.setText(
                        f"<span style='color:{dd_color};'>-{drawdown_pct:.1f}%</span>"
                    )
                else:
                    self.lbl_loss_pct.setText(
                        f"<span style='color:{C['green']};'>0.0%</span>"
                    )
        except Exception:
            pass

    def _refresh_indicator_counts(self):
        """Update FVG, OB, Confluence, MTF FVG, and AMD count labels."""
        self._refresh_mtf_count()
        try:
            if self._fvg_worker:
                fvgs = self._fvg_worker.get_fvgs()
                bull = sum(1 for f in fvgs if f.kind == "BULL")
                bear = sum(1 for f in fvgs if f.kind == "BEAR")
                self.lbl_fvg_count.setText(
                    f"FVGs: {len(fvgs)} total  🟢{bull} bull  🔴{bear} bear")
            elif not self.chk_fvg.isChecked():
                self.lbl_fvg_count.setText("FVGs: — (disabled)")
        except Exception:
            pass

        try:
            if self._ob_worker:
                active_obs = self._ob_worker.get_obs()
                all_obs = self._ob_worker.get_all_obs()
                mitigated = sum(1 for ob in all_obs if ob.mitigated)
                bull = sum(1 for ob in active_obs if ob.kind == "BULL")
                bear = sum(1 for ob in active_obs if ob.kind == "BEAR")
                self.lbl_ob_count.setText(
                    f"OBs: {len(active_obs)} active  "
                    f"🟦{bull} bull  🟣{bear} bear  "
                    f"({mitigated} mitigated)"
                )
            elif not self.chk_ob.isChecked():
                self.lbl_ob_count.setText("OBs: — (disabled)")
        except Exception:
            pass

        try:
            if self._confluence_worker:
                zones = self._confluence_worker.get_zones()
                bull = sum(1 for z in zones if z.kind == "BULL")
                bear = sum(1 for z in zones if z.kind == "BEAR")
                self.lbl_conf_count.setText(
                    f"Confluence: {len(zones)} zones  🟡{bull} bull  🟣{bear} bear"
                )
            elif not self.chk_confluence.isChecked():
                self.lbl_conf_count.setText("Confluence: — (disabled)")
        except Exception:
            pass

    # ── Settings change handlers ──────────────────────────────────

    def _start_amd(self, sym: str = None):
        if sym is None:
            sym = self.sym_combo.currentText().strip() or WATCH_SYMBOL
        if self._amd_worker:
            return
        levels = [lv for lv, chk in self._amd_level_checks.items()
                  if chk.isChecked()]
        self._amd_worker = AMDWatcher(
            symbol=sym,
            visible_levels=levels or DEFAULT_LEVELS,
            show_all_phases=self.chk_amd_all.isChecked(),
            scan_interval=10.0,
            draw_on_chart=True,
            log_fn=lambda m, l="INFO": self._sig.log_line.emit(m, l),
        )
        self._amd_worker.start()

    def _on_base_lot_changed(self, base_lot: float):
        """
        Recompute and refresh the Soft Lot Mode dropdown labels
        whenever the base lot size changes, so the displayed tables
        always reflect the actual lots that will be traded.
        """
        import config as _cfg
        scale = base_lot / 0.01  # ratio vs canonical 0.01 base

        def _scale(table):
            return [round(v * scale, 2) for v in table]

        t1 = _scale(_cfg.SOFT_LOT_TABLE_MODE1)
        t2 = _scale(_cfg.SOFT_LOT_TABLE_MODE2)

        max1 = t1[-1]
        max2 = t2[-1]
        step1 = round(t1[2] - t1[1], 2) if len(t1) > 2 else round(base_lot, 2)
        step2 = round(
            t2[3] - t2[2], 2) if len(t2) > 3 else round(base_lot * 2, 2)

        prev_idx = self.lot_mode_combo.currentIndex()
        self.lot_mode_combo.blockSignals(True)
        self.lot_mode_combo.clear()
        self.lot_mode_combo.addItems([
            f"Mode 1 — {base_lot:.2f} then +{step1:.2f}/touch (max {max1:.2f})",
            f"Mode 2 — {base_lot:.2f} then +{step2:.2f}/touch (max {max2:.2f})",
            "Mode 3 — Classic Martingale (2x doubling, no touch cap)",
        ])
        self.lot_mode_combo.setCurrentIndex(prev_idx)
        self.lot_mode_combo.blockSignals(False)

    def _on_bias_toggled(self, checked: bool):
        """Start or stop bias watcher live when checkbox is toggled."""
        if not self._worker:
            return  # bot not running — will start on next _start()
        sym = self.sym_combo.currentText().strip()
        if checked:
            if self._bias_worker is None:
                self._bias_worker = BiasWatcher(
                    symbol=sym,
                    lookback=self.spin_bias_lookback.value(),
                    scan_interval=self.spin_bias_interval.value(),
                    log_fn=lambda m, l="INFO": self._sig.log_line.emit(m, l),
                    on_results=lambda r: self._sig.bias_update.emit(r),
                )
                self._bias_worker.start()
                self._on_log(
                    f"{datetime.now().strftime('%H:%M:%S')}  "
                    f"🧭  ICT Bias Watcher ENABLED", "NEW"
                )
        else:
            if self._bias_worker:
                self._bias_worker.stop()
                self._bias_worker = None
                self._on_log(
                    f"{datetime.now().strftime('%H:%M:%S')}  "
                    f"🧭  ICT Bias Watcher DISABLED", "INFO"
                )

    def _on_trailing_toggled(self, checked: bool):
        """Toggle trailing SL after R2."""
        if self._worker:
            for src in self._worker._sources.values():
                src._trailing_enabled = checked
                if not checked:
                    src._trailing_buy_floor = 0.0
                    src._trailing_sell_floor = 0.0
        msg = (
            "📈  Trailing SL ENABLED — SL follows price after R2 locks"
            if checked else
            "📈  Trailing SL DISABLED"
        )
        self._sig.log_line.emit(msg, "NEW" if checked else "INFO")

    def _on_partial_exit_toggled(self, checked: bool):
        """Toggle R3 partial exit on running watcher."""
        import config as _cfg
        _cfg.PARTIAL_EXIT_ENABLED = checked
        if self._worker:
            for src in self._worker._sources.values():
                src._partial_exit_r3_enabled = checked
        msg = (
            "📤  Partial Exit (R3) ENABLED — closes 70% at TP, keeps 30% running"
            if checked else
            "📤  Partial Exit (R3) DISABLED"
        )
        self._sig.log_line.emit(msg, "NEW" if checked else "INFO")

    def _on_entry_filter_toggled(self, checked: bool):
        """Toggle OB+FVG entry filter live on the running watcher."""
        import config as _cfg
        _cfg.ENTRY_FILTER_OB_FVG = checked
        if self._worker:
            self._worker._entry_filter_ob_fvg = checked
            for src in self._worker._sources.values():
                src._entry_filter_ob_fvg = checked
        msg = (
            "🟡  OB+FVG Entry Filter ENABLED — only entering on OB+FVG confluence"
            if checked else
            "⬜  OB+FVG Entry Filter DISABLED — entering on any touch"
        )
        self._sig.log_line.emit(msg, "NEW" if checked else "INFO")

    def _on_risk_free_toggled(self, checked: bool):
        """
        Push the Enable Risk-Free checkbox state into the running
        watcher immediately, so toggling it mid-session actually takes
        effect instead of silently doing nothing until the next start.
        """
        if self._worker:
            self._worker.set_risk_free_enabled(checked)
        else:
            self._sig.log_line.emit(
                f"🛡️  Risk-Free will be {'ENABLED' if checked else 'DISABLED'} "
                f"when the bot starts", "INFO"
            )

    def _on_loss_free_toggled(self, checked: bool):
        """Mirror of _on_risk_free_toggled for R1 (loss-free)."""
        if self._worker:
            self._worker.set_loss_free_enabled(checked)
        else:
            self._sig.log_line.emit(
                f"🟩  Loss-Free will be {'ENABLED' if checked else 'DISABLED'} "
                f"when the bot starts", "INFO"
            )

    def _on_amd_toggled(self, state):
        if not self._worker:
            return
        sym = self.sym_combo.currentText().strip() or WATCH_SYMBOL
        if state == Qt.Checked:
            self._start_amd(sym)
        else:
            if self._amd_worker:
                self._amd_worker.stop()
                self._amd_worker = None
            self.lbl_amd_status.setText("AMD: — (disabled)")

    def _on_amd_settings_changed(self):
        if self._amd_worker:
            levels = [lv for lv, chk in self._amd_level_checks.items()
                      if chk.isChecked()]
            self._amd_worker.update_settings(
                visible_levels=levels or DEFAULT_LEVELS,
                show_all_phases=self.chk_amd_all.isChecked(),
            )

    def _refresh_amd_status(self):
        """Update AMD status label from live watcher."""
        try:
            if self._amd_worker:
                s = self._amd_worker.get_status()
                if s:
                    phase_icon = {"A": "🟩", "M": "🟥", "D": "🟦", "C": "⬜"}
                    di = phase_icon.get(s.day,   "")
                    wi = phase_icon.get(s.week,  "")
                    mi = phase_icon.get(s.month, "")
                    self.lbl_amd_status.setText(
                        f"Y:{s.year}  {s.quarter}\n"
                        f"Month: {mi}{s.month}  Week: {wi}{s.week}  Day: {di}{s.day}\n"
                        f"Session: {s.h4}  Hour: {s.h1}  5min: {s.m5}  1min: {s.minute}"
                    )
            elif not self.chk_amd.isChecked():
                self.lbl_amd_status.setText("AMD: — (disabled)")
        except Exception:
            pass

    def _get_selected_mtf_tfs(self) -> list:
        """Currently checked timeframes, in largest-to-smallest order."""
        order = []
        if self.chk_mtf_15m.isChecked():
            order.append("15M")
        if self.chk_mtf_5m.isChecked():
            order.append("5M")
        if self.chk_mtf_1m.isChecked():
            order.append("1M")
        return order

    def _on_mtf_tf_selection_changed(self):
        """
        Enforce a minimum of 2 selected timeframes (re-check the box
        that was just unchecked if it would drop below 2), and keep
        the entry-timeframe dropdown's options in sync with what's
        actually selected.
        """
        boxes = {
            "15M": self.chk_mtf_15m,
            "5M":  self.chk_mtf_5m,
            "1M":  self.chk_mtf_1m,
        }
        selected = self._get_selected_mtf_tfs()

        if len(selected) < 2:
            # Re-check whichever box the user just tried to uncheck —
            # block signals to avoid a recursive triggering loop.
            for tf, box in boxes.items():
                if not box.isChecked():
                    box.blockSignals(True)
                    box.setChecked(True)
                    box.blockSignals(False)
            selected = self._get_selected_mtf_tfs()

        # Keep entry-TF dropdown options matching the current selection
        prev_entry = self.combo_mtf_entry.currentText()
        self.combo_mtf_entry.blockSignals(True)
        self.combo_mtf_entry.clear()
        self.combo_mtf_entry.addItems(selected)
        if prev_entry in selected:
            self.combo_mtf_entry.setCurrentText(prev_entry)
        else:
            self.combo_mtf_entry.setCurrentText(
                selected[-1])  # default: smallest
        self.combo_mtf_entry.blockSignals(False)

        self._on_mtf_settings_changed()

    def _start_mtf(self, sym=None):
        if sym is None:
            sym = self.sym_combo.currentText().strip() or WATCH_SYMBOL
        if self._mtf_fvg_worker:
            return
        from core.order_manager import get_pip_size
        import config as cfg
        pip = get_pip_size(sym)
        self._mtf_fvg_worker = MTFFVGWatcher(
            symbol=sym,
            pip_size=pip,
            selected_tfs=self._get_selected_mtf_tfs(),
            entry_tf=self.combo_mtf_entry.currentText(),
            min_gap_pips=self.spin_mtf_gap.value(),
            lookback_15m=self.spin_mtf_lb15.value(),
            lookback_5m=self.spin_mtf_lb5.value(),
            lookback_1m=self.spin_mtf_lb1.value(),
            max_zones=self.spin_mtf_max.value(),
            max_draw=self.spin_mtf_max.value(),
            draw_on_chart=True,
            poll_interval=1.0,
            log_fn=lambda m, l="INFO": self._sig.log_line.emit(m, l),
        )
        self._mtf_fvg_worker.start()

    def _on_mtf_toggled(self, state):
        if not self._worker:
            return
        sym = self.sym_combo.currentText().strip() or WATCH_SYMBOL
        if state == Qt.Checked:
            self._start_mtf(sym)
        else:
            if self._mtf_fvg_worker:
                self._mtf_fvg_worker.stop()
                self._mtf_fvg_worker = None
            self.lbl_mtf_count.setText("MTF FVG: — (disabled)")

    def _on_mtf_settings_changed(self):
        if self._mtf_fvg_worker:
            self._mtf_fvg_worker.update_settings(
                selected_tfs=self._get_selected_mtf_tfs(),
                entry_tf=self.combo_mtf_entry.currentText(),
                min_gap_pips=self.spin_mtf_gap.value(),
                lookback_15m=self.spin_mtf_lb15.value(),
                lookback_5m=self.spin_mtf_lb5.value(),
                lookback_1m=self.spin_mtf_lb1.value(),
                max_zones=self.spin_mtf_max.value(),
                max_draw=self.spin_mtf_max.value(),
            )

    def _on_fvg_settings_changed(self):
        if self._fvg_worker:
            self._fvg_worker.update_settings(
                min_gap_pips=self.spin_fvg_gap.value(),
                lookback=self.spin_fvg_lookback.value(),
                max_draw=self.spin_fvg_max.value(),
            )

    def _refresh_mtf_count(self):
        try:
            if self._mtf_fvg_worker:
                zones = self._mtf_fvg_worker.get_zones()
                all_z = self._mtf_fvg_worker.get_all_zones()
                mit = sum(1 for z in all_z if z.mitigated)
                bull = sum(1 for z in zones if z.kind == "BULL")
                bear = sum(1 for z in zones if z.kind == "BEAR")
                self.lbl_mtf_count.setText(
                    f"MTF FVG: {len(zones)} active  "
                    f"🟡{bull} bull  🟣{bear} bear  ({mit} mitigated)"
                )
            elif not self.chk_mtf.isChecked():
                self.lbl_mtf_count.setText("MTF FVG: — (disabled)")
        except Exception:
            pass

    def _on_ob_settings_changed(self):
        if self._ob_worker:
            self._ob_worker.update_settings(
                min_impulse_pips=self.spin_ob_impulse.value(),
                lookback=self.spin_ob_lookback.value(),
                swing_lookback=self.spin_ob_swing.value(),
                max_draw=self.spin_ob_max.value(),
            )

    def _on_confluence_settings_changed(self):
        if self._confluence_worker:
            self._confluence_worker.update_settings(
                max_candles_after=self.spin_conf_window.value(),
                require_direction=self.chk_conf_direction.isChecked(),
                max_draw=self.spin_conf_max.value(),
            )

    def _on_symbol_changed(self, sym: str):
        self.lbl_sym_hdr.setText(sym)

    def _detect_symbols(self, max_results: int = 15):
        """
        Pull the REAL tradable symbol list from the connected MT5
        account and rank by ACTUAL SPREAD (tightest first) instead of
        guessing names/keywords — majors are tightly-spread on every
        broker regardless of what that broker happens to call them,
        so this needs zero hardcoded naming knowledge and can't miss
        an instrument just because its name doesn't match a pattern
        I anticipated.

        IMPORTANT (x2):
        1. A symbol only streams live quotes once it's been selected
           into Market Watch — brokers don't send ticks for anything
           you haven't added. So this force-selects every candidate
           first (mt5.symbol_select), rather than only ranking
           whatever happened to already be visible.
        2. Selecting a symbol does NOT make its quote available
           instantly — the terminal has to actually establish that
           subscription with the broker server, which takes a beat.
           Calling symbol_info_tick() immediately after symbol_select()
           routinely returns nothing for anything that wasn't already
           subscribed (this is what was happening: every freshly-
           selected symbol failed the tick check, leaving nothing).
           Fixed by select-ALL-first, wait once, THEN check ticks —
           with one retry round after a second wait for any stragglers.
        """
        self.btn_detect_syms.setEnabled(False)
        self.btn_detect_syms.setText("⏳")
        QApplication.processEvents()
        current = self.sym_combo.currentText().strip()
        try:
            if not mt5.initialize(login=MT5_LOGIN, password=MT5_PASSWORD, server=MT5_SERVER):
                self._on_status(
                    f"⚠️  Could not connect to MT5 to detect symbols: {mt5.last_error()}")
                return
            all_syms = mt5.symbols_get()
            if not all_syms:
                self._on_status(
                    f"⚠️  MT5 returned no symbols (last_error={mt5.last_error()})")
                return

            DISABLED = getattr(mt5, "SYMBOL_TRADE_MODE_DISABLED", 0)
            tradable = [s for s in all_syms if getattr(
                s, "trade_mode", None) != DISABLED]

            # Phase 1: trigger subscriptions for everything not already
            # visible, all up front (don't wait between each one).
            self._on_status(f"⏳  Subscribing to {len(tradable)} symbols…")
            QApplication.processEvents()
            for s in tradable:
                if not getattr(s, "visible", False):
                    mt5.symbol_select(s.name, True)

            # Phase 2: give the terminal a moment to actually start
            # receiving quotes, then check everyone once.
            def _wait(seconds):
                import time as _t
                end = _t.time() + seconds
                while _t.time() < end:
                    QApplication.processEvents()
                    _t.sleep(0.05)

            _wait(1.0)

            def _collect():
                found = []
                pending = []
                for s in tradable:
                    tick = mt5.symbol_info_tick(s.name)
                    bid, ask = (tick.bid, tick.ask) if tick else (0, 0)
                    if bid <= 0 or ask <= 0:
                        # some builds populate bid/ask here too
                        info = mt5.symbol_info(s.name)
                        bid = getattr(info, "bid", 0) or bid
                        ask = getattr(info, "ask", 0) or ask
                    if bid > 0 and ask > 0:
                        point = getattr(s, "point", 0) or 0.00001
                        spread_norm = (ask - bid) / point
                        if spread_norm > 0:
                            found.append((spread_norm, s.name))
                            continue
                    pending.append(s)
                return found, pending

            candidates, pending = _collect()

            # Phase 3: one retry round for anything still not quoting
            # yet — slower brokers/connections need this.
            if pending:
                self._on_status(
                    f"⏳  Waiting on {len(pending)} slower symbols…")
                _wait(2.0)
                tradable = pending
                more, _ = _collect()
                candidates += more

            if not candidates:
                self._on_status(
                    f"⚠️  None of {len(all_syms)} symbols returned a live quote even "
                    f"after selecting them and waiting — market may be fully closed "
                    f"right now, or AutoTrading/connection has an issue"
                )
                return

            candidates.sort(key=lambda c: c[0])
            ordered = [name for _, name in candidates[:max_results]]
            self._populate_combo(ordered, current)
            self._on_status(
                f"✅  Top {len(ordered)} tightest-spread symbols detected "
                f"({len(all_syms)} available, {len(candidates)} with live quotes) — list updated"
            )
        except Exception as e:
            self._on_status(f"⚠️  Symbol detection failed: {e}")
        finally:
            self.btn_detect_syms.setEnabled(True)
            self.btn_detect_syms.setText("🔄")

    def _populate_combo(self, ordered: list, current: str):
        self.sym_combo.blockSignals(True)
        self.sym_combo.clear()
        self.sym_combo.addItems(ordered)
        if current and current in ordered:
            self.sym_combo.setCurrentText(current)
        elif current:
            # Keep whatever the trader had typed even if it's not in
            # the detected list (e.g. needs adding to Market Watch
            # first, or has a wide spread right now) — never silently
            # discard it.
            self.sym_combo.insertItem(0, current)
            self.sym_combo.setCurrentText(current)
        self.sym_combo.blockSignals(False)

    def _init_price(self):
        sym = self.sym_combo.currentText().strip() or WATCH_SYMBOL
        try:
            if mt5.initialize(login=MT5_LOGIN, password=MT5_PASSWORD, server=MT5_SERVER):
                tick = mt5.symbol_info_tick(sym)
                if tick:
                    self.lbl_price.setText(f"{sym}  {tick.bid:.5f}")
        except Exception:
            pass

    def closeEvent(self, event):
        self._stop()
        event.accept()

# ── Entry Point ───────────────────────────────────────────────────


def main():
    app = QApplication(sys.argv)
    app.setStyle("Fusion")
    win = GUI()
    win.show()
    sys.exit(app.exec_())
