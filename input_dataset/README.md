# Happy-Path Pack

A small, self-contained test pack for the invoice-processing (PS-1) prototype. It contains clean vendor invoices that should be approved without issues, plus the procurement reference data they are validated against.

All companies, tax IDs, bank accounts, people and email addresses are fictional.

## Folder structure

```
happy_path_pack/
├── README.md
├── invoices/
└── data/
    ├── vendor_master.csv
    ├── purchase_orders.csv
    ├── po_line_items.csv
    ├── invoice_history.csv
    └── expected_outcomes.csv
```

## `invoices/`

The inputs to the pipeline: vendor invoices as PDFs.

- Every invoice is a clean, machine-readable (text) PDF. There are no scans.
- Each one is a valid, correctly billed invoice, so the expected result for all of them is approval with no warnings.
- Layouts, date formats, number formatting, tax presentation and the way the PO reference is written differ from vendor to vendor, to exercise extraction and normalisation.
- Each invoice is billed to the same fictional buyer.

## `data/`

The stand-in for the company's procurement system. The pipeline reads these files to validate invoices.

| File | Purpose |
|---|---|
| `vendor_master.csv` | The approved vendor list: legal name, tax registration, location, bank details, approval status and billing email. Used to confirm the sender is a known, approved vendor. |
| `purchase_orders.csv` | One row per purchase order: vendor, status, amounts, amount billed so far and remaining balance. Used to find the matching PO and check that billing stays within it. |
| `po_line_items.csv` | The items, quantities and agreed unit prices on each purchase order. Used to compare invoice lines against what was ordered. |
| `invoice_history.csv` | Invoices already processed. Used for duplicate detection and for tracking how much of a PO has been billed. Intentionally empty (headers only) in this pack, since the happy path has no prior invoices. |
| `expected_outcomes.csv` | The answer key: the expected decision for each invoice file. Used to check that the pipeline behaves as intended. |

## How the files relate

```
invoice (PDF)
   └─► vendor            matched via vendor_master.csv
         └─► purchase order    matched via purchase_orders.csv
               ├─► line items      compared with po_line_items.csv
               └─► prior billing   checked against invoice_history.csv
```

`vendor_master.csv`, `purchase_orders.csv` and `po_line_items.csv` are linked by `vendor_id` and `po_number`.
