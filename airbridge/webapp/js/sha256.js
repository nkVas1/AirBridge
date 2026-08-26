/**
 * Streaming SHA-256, per FIPS 180-4.
 *
 * The browser ships a much faster implementation in crypto.subtle, but it
 * is unusable here for two reasons: it is absent outside a secure context,
 * which is exactly the plain-HTTP fallback AirBridge keeps for phones that
 * refuse the certificate; and it only digests a whole buffer at once, so a
 * multi-gigabyte upload would have to be held in memory to be hashed.
 *
 * This one takes the file a chunk at a time, in step with the transfer.
 */

"use strict";

const SHA256 = (() => {
    const K = new Uint32Array([
        0x428a2f98, 0x71374491, 0xb5c0fbcf, 0xe9b5dba5, 0x3956c25b, 0x59f111f1,
        0x923f82a4, 0xab1c5ed5, 0xd807aa98, 0x12835b01, 0x243185be, 0x550c7dc3,
        0x72be5d74, 0x80deb1fe, 0x9bdc06a7, 0xc19bf174, 0xe49b69c1, 0xefbe4786,
        0x0fc19dc6, 0x240ca1cc, 0x2de92c6f, 0x4a7484aa, 0x5cb0a9dc, 0x76f988da,
        0x983e5152, 0xa831c66d, 0xb00327c8, 0xbf597fc7, 0xc6e00bf3, 0xd5a79147,
        0x06ca6351, 0x14292967, 0x27b70a85, 0x2e1b2138, 0x4d2c6dfc, 0x53380d13,
        0x650a7354, 0x766a0abb, 0x81c2c92e, 0x92722c85, 0xa2bfe8a1, 0xa81a664b,
        0xc24b8b70, 0xc76c51a3, 0xd192e819, 0xd6990624, 0xf40e3585, 0x106aa070,
        0x19a4c116, 0x1e376c08, 0x2748774c, 0x34b0bcb5, 0x391c0cb3, 0x4ed8aa4a,
        0x5b9cca4f, 0x682e6ff3, 0x748f82ee, 0x78a5636f, 0x84c87814, 0x8cc70208,
        0x90befffa, 0xa4506ceb, 0xbef9a3f7, 0xc67178f2,
    ]);

    const rotr = (x, n) => (x >>> n) | (x << (32 - n));

    class Hasher {
        constructor() {
            this._h = new Uint32Array([
                0x6a09e667, 0xbb67ae85, 0x3c6ef372, 0xa54ff53a,
                0x510e527f, 0x9b05688c, 0x1f83d9ab, 0x5be0cd19,
            ]);
            this._w = new Uint32Array(64);
            this._buffer = new Uint8Array(64);
            this._buffered = 0;
            this._length = 0; // total bytes seen, for the length suffix
        }

        /**
         * Absorb another slice of the message.
         *
         * @param {Uint8Array|ArrayBuffer} data
         * @returns {Hasher} this, so calls can be chained
         */
        update(data) {
            const bytes = data instanceof Uint8Array ? data : new Uint8Array(data);
            this._length += bytes.length;

            let offset = 0;

            // Top up a partial block left over from last time.
            if (this._buffered > 0) {
                const needed = Math.min(64 - this._buffered, bytes.length);
                this._buffer.set(bytes.subarray(0, needed), this._buffered);
                this._buffered += needed;
                offset = needed;
                if (this._buffered === 64) {
                    this._compress(this._buffer, 0);
                    this._buffered = 0;
                }
            }

            // Consume whole blocks straight from the input.
            while (offset + 64 <= bytes.length) {
                this._compress(bytes, offset);
                offset += 64;
            }

            // Keep the remainder for next time.
            if (offset < bytes.length) {
                this._buffer.set(bytes.subarray(offset), 0);
                this._buffered = bytes.length - offset;
            }

            return this;
        }

        /**
         * Finish the digest.
         *
         * @returns {string} lowercase hex, matching Python's hexdigest()
         */
        hex() {
            const bitLength = this._length * 8;
            const tail = new Uint8Array(this._buffered < 56 ? 64 : 128);
            tail.set(this._buffer.subarray(0, this._buffered), 0);
            tail[this._buffered] = 0x80;

            // The length goes in the last 8 bytes, big-endian. Numbers stay
            // exact to 2^53, which is far past any file a browser can read.
            const high = Math.floor(bitLength / 0x100000000);
            const low = bitLength >>> 0;
            const view = new DataView(tail.buffer);
            view.setUint32(tail.length - 8, high, false);
            view.setUint32(tail.length - 4, low, false);

            for (let offset = 0; offset < tail.length; offset += 64) {
                this._compress(tail, offset);
            }

            let out = "";
            for (let i = 0; i < 8; i++) {
                out += this._h[i].toString(16).padStart(8, "0");
            }
            return out;
        }

        _compress(block, start) {
            const w = this._w;
            for (let i = 0; i < 16; i++) {
                const j = start + i * 4;
                w[i] = (block[j] << 24) | (block[j + 1] << 16) | (block[j + 2] << 8) | block[j + 3];
            }
            for (let i = 16; i < 64; i++) {
                const s0 = rotr(w[i - 15], 7) ^ rotr(w[i - 15], 18) ^ (w[i - 15] >>> 3);
                const s1 = rotr(w[i - 2], 17) ^ rotr(w[i - 2], 19) ^ (w[i - 2] >>> 10);
                w[i] = (w[i - 16] + s0 + w[i - 7] + s1) | 0;
            }

            let [a, b, c, d, e, f, g, h] = this._h;

            for (let i = 0; i < 64; i++) {
                const S1 = rotr(e, 6) ^ rotr(e, 11) ^ rotr(e, 25);
                const ch = (e & f) ^ (~e & g);
                const temp1 = (h + S1 + ch + K[i] + w[i]) | 0;
                const S0 = rotr(a, 2) ^ rotr(a, 13) ^ rotr(a, 22);
                const maj = (a & b) ^ (a & c) ^ (b & c);
                const temp2 = (S0 + maj) | 0;

                h = g;
                g = f;
                f = e;
                e = (d + temp1) | 0;
                d = c;
                c = b;
                b = a;
                a = (temp1 + temp2) | 0;
            }

            const s = this._h;
            s[0] = (s[0] + a) | 0;
            s[1] = (s[1] + b) | 0;
            s[2] = (s[2] + c) | 0;
            s[3] = (s[3] + d) | 0;
            s[4] = (s[4] + e) | 0;
            s[5] = (s[5] + f) | 0;
            s[6] = (s[6] + g) | 0;
            s[7] = (s[7] + h) | 0;
        }
    }

    return {
        /** Start a new streaming digest. */
        create: () => new Hasher(),
        /** One-shot digest, for short inputs and for self-checking. */
        hex: (data) => new Hasher().update(data).hex(),
    };
})();
