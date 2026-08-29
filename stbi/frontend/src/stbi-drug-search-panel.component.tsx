import React, { useCallback, useMemo, useState } from 'react';
import { useTranslation } from 'react-i18next';
import {
  Button,
  InlineNotification,
  Search,
  SkeletonText,
  Tag,
  Tile,
} from '@carbon/react';
import { useDebounce, useLayoutType } from '@openmrs/esm-framework';
import {
  buildOrderBasketItem,
  useFormularyMatch,
  useStbiSearch,
  useStbiStats,
  type FormularyDrug,
  type StbiDocument,
} from './stbi.resource';
import styles from './stbi-drug-search-panel.scss';

/**
 * Props yang dioper OpenMRS ke pengisi `drug-search-slot`.
 *
 * Perhatikan yang TIDAK ada di sini: teks yang sedang diketik pengguna pada
 * kotak pencarian bawaan. Slot tidak mengopernya, jadi panel ini menyediakan
 * kotak pencariannya sendiri.
 */
interface PanelProps {
  openOrderForm?: (item: unknown) => void;
  isSearching?: boolean;
  visit?: unknown;
  daysDurationUnit?: { display: string; uuid: string };
}

function ResultTile({
  doc,
  visit,
  daysDurationUnit,
  openOrderForm,
}: {
  doc: StbiDocument;
  visit: unknown;
  daysDurationUnit: PanelProps['daysDurationUnit'];
  openOrderForm: PanelProps['openOrderForm'];
}) {
  const { t } = useTranslation();
  const [expanded, setExpanded] = useState(false);
  const { drugs } = useFormularyMatch(doc);

  const onOrder = useCallback(
    (drug: FormularyDrug) => {
      if (typeof openOrderForm !== 'function') {
        return;
      }
      try {
        openOrderForm(buildOrderBasketItem(drug, visit, daysDurationUnit));
      } catch (err) {
        // Bentuk item keranjang order bisa berubah antar versi OpenMRS. Bila
        // itu terjadi, panel tetap berguna sebagai referensi - kegagalan di
        // sini tidak boleh menjatuhkan seluruh layar keranjang order.
        console.error('[stbi] gagal membuka formulir order', err);
      }
    },
    [openOrderForm, visit, daysDurationUnit],
  );

  const sideEffects = expanded ? doc.side_effects : doc.side_effects.slice(0, 5);

  return (
    <Tile className={styles.result}>
      <div className={styles.resultHead}>
        <div>
          <p className={styles.resultTitle}>{doc.title}</p>
          <p className={styles.resultMeta}>
            {doc.therapeutic_class}
            {doc.brand_count > 1 && ` · ${t('stbiBrands', '{{count}} brands', { count: doc.brand_count })}`}
            {doc.title_source === 'brand' && ` · ${t('stbiBrandNameTitle', 'brand name')}`}
          </p>
        </div>
        <span className={styles.score} title={doc.explain.map((e) => `${e.term}: ${e.contribution}`).join('\n')}>
          {doc.score.toFixed(1)}
        </span>
      </div>

      <p className={styles.uses}>{doc.uses.join(' · ')}</p>

      <div className={styles.tags}>
        {doc.habit_forming && (
          <Tag type="magenta" size="sm">
            {t('stbiHabitForming', 'Habit forming')}
          </Tag>
        )}
        {drugs.slice(0, 3).map((drug) => (
          <Button
            key={drug.uuid}
            className={styles.formularyChip}
            kind="ghost"
            size="sm"
            onClick={() => onOrder(drug)}
          >
            ✓ {drug.display}
          </Button>
        ))}
      </div>

      {sideEffects.length > 0 && (
        <p className={styles.detailLine}>
          <span className={styles.detailLabel}>{t('stbiSideEffects', 'Side effects')}</span>
          {sideEffects.join(', ')}
          {!expanded && doc.side_effects.length > 5 && (
            <button type="button" className={styles.moreButton} onClick={() => setExpanded(true)}>
              +{doc.side_effects.length - 5}
            </button>
          )}
        </p>
      )}

      {expanded && doc.substitutes.length > 0 && (
        <p className={styles.detailLine}>
          <span className={styles.detailLabel}>{t('stbiSubstitutes', 'Substitutes')}</span>
          {doc.substitutes.slice(0, 5).join(', ')}
        </p>
      )}
    </Tile>
  );
}

export default function StbiDrugSearchPanel({
  openOrderForm,
  visit,
  daysDurationUnit,
}: PanelProps) {
  const { t } = useTranslation();
  const isTablet = useLayoutType() === 'tablet';
  const [term, setTerm] = useState('');
  const debounced = useDebounce(term, 300);
  const stats = useStbiStats();
  const { results, totalMatched, isLoading, error } = useStbiSearch(debounced);

  const subtitle = useMemo(
    () =>
      stats
        ? t('stbiPanelSubtitle', 'BM25 ranking over {{count}} clinical profiles', {
            count: stats.documents,
          })
        : '',
    [stats, t],
  );

  return (
    <section className={styles.panel} aria-label={t('stbiPanelTitle', 'Drug reference search')}>
      <header className={styles.header}>
        <h2 className={styles.title}>{t('stbiPanelTitle', 'Drug reference search')}</h2>
        {subtitle && <span className={styles.subtitle}>{subtitle}</span>}
      </header>

      <Search
        className={styles.search}
        labelText={t('stbiSearchPlaceholder', 'Search by condition, e.g. "hypertension"')}
        placeholder={t('stbiSearchPlaceholder', 'Search by condition, e.g. "hypertension"')}
        size={isTablet ? 'lg' : 'md'}
        value={term}
        onChange={(event) => setTerm(event.target.value ?? '')}
        onClear={() => setTerm('')}
      />

      {/* Disclaimer diletakkan di dalam panel, bukan di footer halaman, supaya
          terbaca bersamaan dengan hasilnya. */}
      <InlineNotification
        className={styles.disclaimer}
        kind="info"
        lowContrast
        hideCloseButton
        title=""
        subtitle={t(
          'stbiDisclaimer',
          "External reference for education only. Not this facility's formulary, and not a prescribing recommendation.",
        )}
      />

      {error && (
        <InlineNotification
          kind="error"
          lowContrast
          hideCloseButton
          title={t('stbiError', 'Drug reference service unavailable')}
        />
      )}

      {isLoading && (
        <div className={styles.skeleton}>
          <SkeletonText paragraph lineCount={3} />
        </div>
      )}

      {!isLoading && debounced.trim() && !error && results.length === 0 && (
        <p className={styles.empty}>{t('stbiNoResults', 'No drug reference entries for "{{term}}"', { term: debounced })}</p>
      )}

      {results.length > 0 && (
        <>
          <p className={styles.count}>
            {t('stbiMatchCount', '{{count}} matching documents', { count: totalMatched })}
          </p>
          <div className={styles.results}>
            {results.map((doc) => (
              <ResultTile
                key={doc.id}
                doc={doc}
                visit={visit}
                daysDurationUnit={daysDurationUnit}
                openOrderForm={openOrderForm}
              />
            ))}
          </div>
        </>
      )}
    </section>
  );
}
