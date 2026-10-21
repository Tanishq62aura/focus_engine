/**
 * static/js/offlineQueue.js
 * Focus Engine – Local-First Offline Synchronization & Idempotency Subsystem
 *
 * Implements an IndexedDB-backed outbox queue for mutating requests (POST, PUT, PATCH),
 * persistent session recovery anchoring, and an automated background drain worker.
 */

(function (global) {
  'use strict';

  const DB_NAME = 'FocusEngineOfflineDB';
  const DB_VERSION = 1;
  const STORE_OUTBOX = 'outbox';
  const STORE_SESSION_ANCHOR = 'session_anchor';

  /**
   * Deterministic / standard RFC4122 v4 UUID generator.
   */
  function generateUUID() {
    if (typeof crypto !== 'undefined' && typeof crypto.randomUUID === 'function') {
      return crypto.randomUUID();
    }
    return 'xxxxxxxx-xxxx-4xxx-yxxx-xxxxxxxxxxxx'.replace(/[xy]/g, function (c) {
      const r = (Math.random() * 16) | 0;
      const v = c === 'x' ? r : (r & 0x3) | 0x8;
      return v.toString(16);
    });
  }

  /**
   * Promise wrapper for opening IndexedDB.
   */
  function openDatabase() {
    return new Promise((resolve, reject) => {
      if (typeof indexedDB === 'undefined') {
        return reject(new Error('IndexedDB is not supported in this environment.'));
      }
      const request = indexedDB.open(DB_NAME, DB_VERSION);

      request.onupgradeneeded = function (event) {
        const db = event.target.result;

        // 1. Outbox store for FIFO mutation replay
        if (!db.objectStoreNames.contains(STORE_OUTBOX)) {
          const outboxStore = db.createObjectStore(STORE_OUTBOX, { keyPath: 'mutation_id' });
          outboxStore.createIndex('created_at', 'created_at', { unique: false });
        }

        // 2. Session Anchor store for timer recovery
        if (!db.objectStoreNames.contains(STORE_SESSION_ANCHOR)) {
          db.createObjectStore(STORE_SESSION_ANCHOR, { keyPath: 'key' });
        }
      };

      request.onsuccess = function (event) {
        resolve(event.target.result);
      };

      request.onerror = function (event) {
        reject(event.target.error || new Error('Failed to open FocusEngineOfflineDB'));
      };
    });
  }

  /**
   * Offline Synchronization Engine managing Outbox and Session Recovery
   */
  class OfflineSyncEngine {
    constructor() {
      this.dbPromise = openDatabase().catch((err) => {
        console.warn('[OfflineSync] IndexedDB initialization deferred/failed:', err);
        return null;
      });
      this.isDraining = false;
      this.drainInterval = null;

      this._initWorkers();
    }

    _initWorkers() {
      if (typeof window === 'undefined') return;

      // 1. Trigger replay when network connectivity returns
      window.addEventListener('online', () => {
        console.info('[OfflineSync] Online network event detected. Triggering outbox drain.');
        this.replayOutbox();
      });

      // 2. Replay periodically every 15 seconds
      this.drainInterval = setInterval(() => {
        if (typeof navigator !== 'undefined' && navigator.onLine) {
          this.replayOutbox();
        }
      }, 15000);
    }

    async _getDB() {
      const db = await this.dbPromise;
      if (!db) {
        throw new Error('IndexedDB unavailable');
      }
      return db;
    }

    // -------------------------------------------------------------------------
    // Outbox Queue Management
    // -------------------------------------------------------------------------
    async enqueueMutation(item) {
      const db = await this._getDB();
      return new Promise((resolve, reject) => {
        const tx = db.transaction(STORE_OUTBOX, 'readwrite');
        const store = tx.objectStore(STORE_OUTBOX);
        const record = {
          mutation_id: item.mutation_id || generateUUID(),
          endpoint: item.endpoint,
          method: (item.method || 'POST').toUpperCase(),
          payload: item.payload || null,
          headers: item.headers || {},
          created_at: item.created_at || new Date().toISOString(),
          retry_count: item.retry_count || 0,
        };

        const req = store.put(record);
        req.onsuccess = () => resolve(record);
        req.onerror = () => reject(req.error);
      });
    }

    async getOutboxItems() {
      const db = await this._getDB();
      return new Promise((resolve, reject) => {
        const tx = db.transaction(STORE_OUTBOX, 'readonly');
        const store = tx.objectStore(STORE_OUTBOX);
        const req = store.getAll();

        req.onsuccess = () => {
          const items = req.result || [];
          // Strict FIFO order by created_at ISO timestamp
          items.sort((a, b) => new Date(a.created_at).getTime() - new Date(b.created_at).getTime());
          resolve(items);
        };
        req.onerror = () => reject(req.error);
      });
    }

    async evictMutation(mutation_id) {
      const db = await this._getDB();
      return new Promise((resolve, reject) => {
        const tx = db.transaction(STORE_OUTBOX, 'readwrite');
        const store = tx.objectStore(STORE_OUTBOX);
        const req = store.delete(mutation_id);
        req.onsuccess = () => resolve(true);
        req.onerror = () => reject(req.error);
      });
    }

    async updateOutboxItem(item) {
      const db = await this._getDB();
      return new Promise((resolve, reject) => {
        const tx = db.transaction(STORE_OUTBOX, 'readwrite');
        const store = tx.objectStore(STORE_OUTBOX);
        const req = store.put(item);
        req.onsuccess = () => resolve(item);
        req.onerror = () => reject(req.error);
      });
    }

    // -------------------------------------------------------------------------
    // Session Recovery Anchor Store
    // -------------------------------------------------------------------------
    async saveSessionAnchor({ active_session_id, start_time_iso, target_minutes }) {
      const db = await this._getDB();
      return new Promise((resolve, reject) => {
        const tx = db.transaction(STORE_SESSION_ANCHOR, 'readwrite');
        const store = tx.objectStore(STORE_SESSION_ANCHOR);
        const record = {
          key: 'current_session',
          active_session_id,
          start_time_iso,
          target_minutes,
          updated_at: new Date().toISOString(),
        };
        const req = store.put(record);
        req.onsuccess = () => resolve(record);
        req.onerror = () => reject(req.error);
      });
    }

    async getSessionAnchor(key = 'current_session') {
      const db = await this._getDB();
      return new Promise((resolve, reject) => {
        const tx = db.transaction(STORE_SESSION_ANCHOR, 'readonly');
        const store = tx.objectStore(STORE_SESSION_ANCHOR);
        const req = store.get(key);
        req.onsuccess = () => resolve(req.result || null);
        req.onerror = () => reject(req.error);
      });
    }

    async clearSessionAnchor(key = 'current_session') {
      const db = await this._getDB();
      return new Promise((resolve, reject) => {
        const tx = db.transaction(STORE_SESSION_ANCHOR, 'readwrite');
        const store = tx.objectStore(STORE_SESSION_ANCHOR);
        const req = store.delete(key);
        req.onsuccess = () => resolve(true);
        req.onerror = () => reject(req.error);
      });
    }

    // -------------------------------------------------------------------------
    // Replay & Drain Worker
    // -------------------------------------------------------------------------
    async replayOutbox() {
      if (this.isDraining) return;
      if (typeof navigator !== 'undefined' && !navigator.onLine) return;

      this.isDraining = true;
      try {
        const items = await this.getOutboxItems();
        if (!items || items.length === 0) {
          return;
        }

        console.info(`[OfflineSync] Replaying ${items.length} queued offline mutations in FIFO sequence.`);

        for (const item of items) {
          const reqHeaders = Object.assign(
            {
              'Content-Type': 'application/json',
              'X-Idempotency-Key': item.mutation_id,
            },
            item.headers || {}
          );

          // Auto-inject Supabase JWT token from local persistence if not already set
          if (!reqHeaders['Authorization'] && typeof localStorage !== 'undefined') {
            try {
              const rawAuth = localStorage.getItem('focus_auth_token') ||
                              localStorage.getItem('supabase.auth.token') ||
                              localStorage.getItem('sb-access-token');
              if (rawAuth) {
                let token = rawAuth;
                if (rawAuth.startsWith('{')) {
                  const parsed = JSON.parse(rawAuth);
                  token = parsed?.currentSession?.access_token || parsed?.access_token || rawAuth;
                }
                if (token && typeof token === 'string') {
                  reqHeaders['Authorization'] = `Bearer ${token}`;
                }
              }
            } catch (_) {}
          }

          let response;
          try {
            response = await fetch(item.endpoint, {
              method: item.method,
              headers: reqHeaders,
              body: typeof item.payload === 'string' ? item.payload : (item.payload ? JSON.stringify(item.payload) : undefined),
            });
          } catch (netErr) {
            console.warn(`[OfflineSync] Network drop during replay of ${item.mutation_id}. Halting FIFO replay:`, netErr);
            item.retry_count = (item.retry_count || 0) + 1;
            await this.updateOutboxItem(item);
            break; // Strict FIFO: Halt replay pipeline on network drop
          }

          // Handlers
          if (response.ok || response.status === 409) {
            // 2xx Success or 409 Conflict: Mutation succeeded or already processed. Evict.
            console.info(`[OfflineSync] Mutation ${item.mutation_id} accepted (${response.status}). Evicting from outbox.`);
            await this.evictMutation(item.mutation_id);
          } else if (response.status >= 400 && response.status < 500) {
            // Client error (4xx except 409): Schema validation / bad request.
            // Log and drop to prevent permanent head-of-line blocking.
            console.warn(`[OfflineSync] Dropping client-error mutation ${item.mutation_id} (HTTP ${response.status}).`);
            await this.evictMutation(item.mutation_id);
          } else if (response.status >= 500) {
            // Server error (5xx): Temporary backend issue.
            // Halt replay pipeline to maintain strict ordering; retry on next tick.
            console.error(`[OfflineSync] Server error (${response.status}) on mutation ${item.mutation_id}. Halting pipeline for retry.`);
            item.retry_count = (item.retry_count || 0) + 1;
            await this.updateOutboxItem(item);
            break; // Strict FIFO: Halt replay pipeline
          }
        }
      } catch (err) {
        console.error('[OfflineSync] Error during outbox drain cycle:', err);
      } finally {
        this.isDraining = false;
      }
    }
  }

  const focusSyncEngine = new OfflineSyncEngine();

  /**
   * Robust fetch wrapper with offline queue fallback for mutating HTTP methods.
   *
   * @param {string} endpoint - Target URL or path
   * @param {RequestInit} [options] - Standard Fetch API options
   * @returns {Promise<Response>}
   */
  async function focusApiFetch(endpoint, options) {
    const opts = options || {};
    const method = (opts.method || 'GET').toUpperCase();
    const isMutating = ['POST', 'PUT', 'PATCH'].includes(method);
    const isOffline = typeof navigator !== 'undefined' && !navigator.onLine;

    // 1. Offline upfront intercept
    if (isOffline && isMutating) {
      const mutation_id = generateUUID();
      let payload = opts.body;
      if (payload && typeof payload !== 'string') {
        try { payload = JSON.stringify(payload); } catch (_) {}
      }

      await focusSyncEngine.enqueueMutation({
        mutation_id,
        endpoint,
        method,
        payload,
        headers: opts.headers || {},
        created_at: new Date().toISOString(),
        retry_count: 0,
      });

      console.info(`[focusApiFetch] Offline detected: queued mutation ${mutation_id} for ${method} ${endpoint}`);
      return new Response(JSON.stringify({ status: 'QUEUED_OFFLINE', mutation_id }), {
        status: 202,
        statusText: 'Queued Offline',
        headers: {
          'Content-Type': 'application/json',
          'X-Offline-Queued': 'true',
          'X-Idempotency-Key': mutation_id,
        },
      });
    }

    // 2. Online request attempt
    try {
      return await fetch(endpoint, opts);
    } catch (fetchErr) {
      const isNetworkError =
        fetchErr instanceof TypeError ||
        (fetchErr && fetchErr.name === 'TypeError') ||
        (fetchErr && fetchErr.message && (
          fetchErr.message.toLowerCase().includes('failed to fetch') ||
          fetchErr.message.toLowerCase().includes('network') ||
          fetchErr.message.toLowerCase().includes('offline')
        ));

      if (isMutating && isNetworkError) {
        const mutation_id = generateUUID();
        let payload = opts.body;
        if (payload && typeof payload !== 'string') {
          try { payload = JSON.stringify(payload); } catch (_) {}
        }

        await focusSyncEngine.enqueueMutation({
          mutation_id,
          endpoint,
          method,
          payload,
          headers: opts.headers || {},
          created_at: new Date().toISOString(),
          retry_count: 0,
        });

        console.info(`[focusApiFetch] Network dropped during fetch: queued mutation ${mutation_id} for ${method} ${endpoint}`);
        return new Response(JSON.stringify({ status: 'QUEUED_OFFLINE', mutation_id }), {
          status: 202,
          statusText: 'Queued Offline',
          headers: {
            'Content-Type': 'application/json',
            'X-Offline-Queued': 'true',
            'X-Idempotency-Key': mutation_id,
          },
        });
      }

      throw fetchErr;
    }
  }

  // Exports
  if (typeof global !== 'undefined') {
    global.FocusEngineOfflineSync = {
      OfflineSyncEngine,
      focusSyncEngine,
      focusApiFetch,
      generateUUID,
    };
    global.focusSyncEngine = focusSyncEngine;
    global.focusApiFetch = focusApiFetch;
  }

  if (typeof module !== 'undefined' && module.exports) {
    module.exports = {
      OfflineSyncEngine,
      focusSyncEngine,
      focusApiFetch,
      generateUUID,
    };
  }
})(typeof window !== 'undefined' ? window : typeof globalThis !== 'undefined' ? globalThis : this);
