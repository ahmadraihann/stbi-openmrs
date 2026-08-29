import { openmrsFetch, restBaseUrl } from '@openmrs/esm-framework';
import useSWR from 'swr';

/** Satu profil klinis hasil deduplikasi dataset 250k Medicines. */
export interface StbiDocument {
  id: string;
  title: string;
  title_source: 'chemical_class' | 'action_class' | 'brand' | 'none';
  uses: string[];
  side_effects: string[];
  therapeutic_class: string;
  chemical_class: string;
  action_class: string;
  habit_forming: boolean;
  brands: string[];
  brand_count: number;
  substitutes: string[];
  score: number;
  explain: Array<{ term: string; contribution: number }>;
}

export interface StbiSearchResponse {
  query: string;
  terms: string[];
  totalMatched: number;
  returned: number;
  results: StbiDocument[];
}

export interface StbiStats {
  rows_in_csv: number;
  documents: number;
  compression: number;
  unique_indications: number;
  k1: number;
  b: number;
}

/** Obat dari kamus OpenMRS - bentuk representasi disamakan dengan yang dipakai
 *  layar keranjang order, supaya objek ini bisa langsung dioper ke formulir. */
export interface FormularyDrug {
  uuid: string;
  display: string;
  name: string;
  strength: string;
  dosageForm: { display: string; uuid: string } | null;
  concept: { display: string; uuid: string } | null;
}

const DRUG_REPRESENTATION =
  'custom:(uuid,display,name,strength,dosageForm:(display,uuid),concept:(display,uuid))';

const fetcher = <T,>(url: string) => openmrsFetch<T>(url).then((res) => res.data);

/**
 * Pencarian BM25 ke layanan STBI.
 *
 * Endpoint ini berada di bawah /ws/rest/v1/stbi supaya `openmrsFetch` bisa
 * dipakai apa adanya - permintaan berjalan pada origin yang sama dan membawa
 * sesi OpenMRS. Ketika BM25 nanti pindah ke OMOD Java + Elasticsearch, alamat
 * dan bentuk balasannya tidak berubah, jadi berkas ini tidak perlu disentuh.
 */
export function useStbiSearch(term: string, limit = 8) {
  const trimmed = term?.trim() ?? '';
  const url = trimmed
    ? `${restBaseUrl}/stbi/search?q=${encodeURIComponent(trimmed)}&limit=${limit}`
    : null;

  const { data, error, isLoading } = useSWR<StbiSearchResponse>(url, fetcher, {
    revalidateOnFocus: false,
    keepPreviousData: true,
  });

  return {
    results: data?.results ?? [],
    totalMatched: data?.totalMatched ?? 0,
    isLoading: Boolean(url) && isLoading,
    error,
  };
}

export function useStbiStats() {
  const { data } = useSWR<StbiStats>(`${restBaseUrl}/stbi/stats`, fetcher, {
    revalidateOnFocus: false,
  });
  return data;
}

/**
 * Mencari padanan sebuah profil klinis di kamus obat OpenMRS.
 *
 * Ini jembatan antara dua semesta yang tidak pernah cocok sepenuhnya: dataset
 * berisi 248 ribu merek dagang India, sedangkan fasilitas ini hanya punya 323
 * obat yang benar-benar bisa diresepkan. Pencocokan dicoba lewat kelas kimia
 * lebih dulu (paling dekat dengan zat aktif), lalu lewat nama merek.
 *
 * Hasil nol adalah keadaan yang wajar dan harus ditampilkan apa adanya - itu
 * justru informasi penting bagi dokter: obat ini tidak tersedia di sini.
 */
export function useFormularyMatch(doc: StbiDocument | null) {
  // Satu kandidat saja terlalu sering meleset. Kelas kimia sering berisi nama
  // golongan ("Imidazoline derivative") dan bukan zat aktif, sedangkan nama
  // merek India ("Amlobet Tablet") tidak pernah ada di kamus OpenMRS. Karena
  // itu beberapa kandidat dicoba berurutan dan yang pertama berhasil dipakai.
  const candidates = doc
    ? [doc.chemical_class, doc.action_class, ...doc.brands.slice(0, 2)]
        // Buang kekuatan, bentuk sediaan, dan kata golongan yang tak pernah
        // muncul sebagai nama obat di kamus.
        .map((c) => (c || '').split(/[\s(/]/)[0])
        .filter((c) => c.length > 3 && !/^(derivative|generation|third|first|second)$/i.test(c))
    : [];

  const makeUrl = (needle: string | undefined) =>
    needle ? `${restBaseUrl}/drug?q=${encodeURIComponent(needle)}&v=${DRUG_REPRESENTATION}` : null;

  // Jumlah hook harus tetap antar-render, jadi dua kandidat teratas dicoba
  // paralel dan bukan berantai.
  const first = useSWR<{ results: FormularyDrug[] }>(makeUrl(candidates[0]), fetcher, {
    revalidateOnFocus: false,
  });
  const second = useSWR<{ results: FormularyDrug[] }>(makeUrl(candidates[1]), fetcher, {
    revalidateOnFocus: false,
  });

  const drugs = first.data?.results?.length ? first.data.results : (second.data?.results ?? []);

  return { drugs, isLoading: first.isLoading || second.isLoading };
}

/**
 * Membentuk item keranjang order dari obat kamus OpenMRS.
 *
 * Bentuk objek disalin dari yang dibangun modul esm-patient-medications-app
 * sendiri saat pengguna menekan "Order form" pada hasil pencarian bawaan, agar
 * formulir order menerima persis apa yang ia harapkan.
 */
export function buildOrderBasketItem(drug: FormularyDrug, visit: unknown, daysDurationUnit: any) {
  const dosageForm = drug.dosageForm
    ? { value: drug.dosageForm.display, valueCoded: drug.dosageForm.uuid }
    : null;

  return {
    action: 'NEW',
    display: drug.display,
    drug,
    unit: dosageForm,
    dosage: null,
    frequency: null,
    route: null,
    commonMedicationName: drug.display,
    isFreeTextDosage: false,
    patientInstructions: '',
    asNeeded: false,
    asNeededCondition: null,
    scheduledDate: new Date(),
    duration: null,
    durationUnit: daysDurationUnit
      ? { value: daysDurationUnit.display, valueCoded: daysDurationUnit.uuid }
      : null,
    pillsDispensed: null,
    numRefills: null,
    freeTextDosage: '',
    indication: '',
    quantityUnits: dosageForm,
    visit,
  };
}
