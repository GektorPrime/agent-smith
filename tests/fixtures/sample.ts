import { readFileSync } from 'fs';
import path from 'path';

interface Cacheable {
  key: string;
  touch(): void;
}

const DEFAULT_TTL = 60;

class CacheEntry implements Cacheable {
  key: string;

  constructor(key: string) {
    this.key = key;
  }

  touch(): void {
    readFileSync(path.join('/tmp', this.key));
  }
}

function createEntry(name: string): CacheEntry {
  return new CacheEntry(name);
}
