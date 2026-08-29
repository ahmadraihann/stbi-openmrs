import { getAsyncLifecycle } from '@openmrs/esm-framework';

const moduleName = '@openmrs/esm-stbi-app';

const options = {
  featureName: 'stbi',
  moduleName,
};

export const importTranslation = require.context('../translations', false, /.json$/, 'lazy');

/**
 * Panel temu balik informasi obat berbasis BM25.
 *
 * Dipasang pada `drug-search-slot`, yaitu titik tepat di bawah kotak pencarian
 * obat pada keranjang order dan tepat di atas daftar hasil bawaan OpenMRS.
 * Pencarian bawaan mencocokkan potongan nama obat pada kamus 323 entri;
 * panel ini menjawab query berupa nama penyakit, yang tidak bisa dijawab
 * pencarian bawaan sama sekali.
 */
export const stbiDrugSearchPanel = getAsyncLifecycle(
  () => import('./stbi-drug-search-panel.component'),
  options,
);
