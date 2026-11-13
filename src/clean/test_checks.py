"""실제 저장 결과의 독립 검산 및 새 오류를 탐지하는 회귀 검사."""
import copy
import hashlib
import tempfile
import unittest
from pathlib import Path

import pandas as pd

from src.common.io import ROOT, active_od, load_all, load_config, load_file
from src.clean.rules import BENCH, MASTER, ORDERS, SITES, classify_area, validate
from src.travel_time import read_sectioned_csv


class CleanChecks(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.cfg=load_config()
        cls.raw=load_all(source="raw",allow_raw=True)
        cls.clean=load_all()

    def test_source_hashes(self):
        checks=pd.read_csv(ROOT/"outputs/clean_source_hashes.csv",encoding="utf-8-sig")
        self.assertEqual(len(checks),24)
        for r in checks.itertuples():
            self.assertEqual(hashlib.sha256((ROOT/"data"/r.file).read_bytes()).hexdigest(),r.sha256_before)
            self.assertEqual(r.sha256_before,r.sha256_after)

    def test_default_source_and_raw_guard(self):
        self.assertEqual(len(load_file(SITES)),68)
        with self.assertRaises(ValueError):
            load_file(SITES,source="raw")
        with self.assertRaises(ValueError):
            load_file("../../data/01_dong_master.csv")

    def test_sectioned_leading_zero_identifiers(self):
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/"sample.csv"
            path.write_text("# A\nadm_cd2,adm_cd,site_id\n0012345678,00123456,0001\n\n# B\nhour,speed\n0,20\n# C\n# 설명만 있는 구획\n",encoding="utf-8-sig")
            sections=read_sectioned_csv(path)
            self.assertEqual(sections["A"].iloc[0].tolist(),["0012345678","00123456","0001"])
            self.assertEqual(list(sections),["A","B"])
        for name in self.cfg["io"]["sectioned_files"]:
            self.assertEqual((ROOT/"data"/name).read_bytes(),(ROOT/"outputs/clean"/name).read_bytes())

    def test_order_partition_and_financial_conservation(self):
        raw=self.raw[ORDERS].drop_duplicates().set_index("order_id")
        combined=pd.concat([self.clean[ORDERS],load_file("05_pilot_orders_test.csv"),load_file("05_pilot_orders_cancelled.csv")]).set_index("order_id")
        self.assertTrue(combined.index.is_unique)
        self.assertEqual(set(raw.index),set(combined.index))
        self.assertEqual(len(self.raw[ORDERS])-len(raw),237)
        for col in ["basket_krw","delivery_fee_krw","rider_pay_alloc_krw","late_coupon_krw"]:
            pd.testing.assert_series_equal(raw[col].sort_index(),combined[col].sort_index(),check_names=False)

    def test_approved_exceptions(self):
        o=self.clean[ORDERS].set_index("order_id")
        row=o.loc["P1-260917-0285"]
        self.assertTrue(pd.isna(row.weight_kg))
        self.assertEqual([row.weight_unresolved,row.demand_included,row.rider_trip_eligible,row.weight_analysis_eligible],[1,1,0,0])
        self.assertEqual(int(o.weight_kg.isna().sum()),1)
        missing=o.delivered_ts.isna()
        self.assertEqual(int(missing.sum()),777)
        self.assertTrue(o.loc[missing,"demand_included"].eq(1).all())
        self.assertTrue(o.loc[missing,"delivery_kpi_eligible"].eq(0).all())
        b=self.clean[BENCH]
        pd.testing.assert_frame_equal(self.raw[BENCH],b[self.raw[BENCH].columns])
        self.assertEqual(int(b.time_mismatch.sum()),26)
        self.assertTrue(b.loc[b.time_mismatch.eq(1),"order_time"].str.endswith(":00").all())

    def test_timestamp_shift_all_columns(self):
        clean=pd.concat([self.clean[ORDERS],load_file("05_pilot_orders_test.csv"),load_file("05_pilot_orders_cancelled.csv")]).set_index("order_id")
        raw=self.raw[ORDERS].drop_duplicates().set_index("order_id").loc[clean.index]
        shifted=clean.timestamp_shifted.eq(1)
        self.assertEqual(int(shifted.sum()),271)
        for col in self.cfg["cleaning"]["timestamp_columns"]:
            a=pd.to_datetime(raw[col]); b=pd.to_datetime(clean[col])
            self.assertTrue(a.isna().eq(b.isna()).all())
            valid=a.notna()
            self.assertTrue((b-a)[valid & shifted].eq(pd.Timedelta(hours=9)).all())
            self.assertTrue((b-a)[valid & ~shifted].eq(pd.Timedelta(0)).all())

    def test_candidates_coordinates_and_od(self):
        sites=self.clean[SITES].set_index("site_id")
        self.assertEqual(set(self.raw[SITES].site_id)-set(sites.index),{"C025","C056","C069"})
        self.assertEqual(sites.hub_type.value_counts().to_dict(),{"S":28,"M":27,"L":13})
        master=self.clean[MASTER].set_index("adm_cd2")
        c040=sites.loc["C040"]
        self.assertEqual(c040.lat,master.loc[c040.adm_cd2,"lat"])
        self.assertEqual(c040.lon,master.loc[c040.adm_cd2,"lon"])
        pd.testing.assert_frame_equal(self.raw["13_od_site_dong.csv"],self.clean["13_od_site_dong.csv"])
        self.assertEqual(len(active_od()),68*427)

    def test_area_boundaries(self):
        area=pd.Series([149.9,150,329.999,330,659.999,660,1000,1000.1])
        actual=classify_area(area,self.cfg).fillna("OUT").tolist()
        self.assertEqual(actual,["OUT","S","S","M","M","L","L","OUT"])

    def test_new_defects_and_removed_flags_fail_validation(self):
        modified=dict(self.clean)
        modified[BENCH]=self.clean[BENCH].copy()
        modified[BENCH].loc[0,"cust_lat"]=999
        modified[BENCH].loc[1,"adm_cd2"]="0000000000"
        modified[BENCH].loc[:,"time_mismatch"]=0
        a=validate(modified,self.cfg,stage="clean",raw_site_ids=self.raw[SITES].site_id)
        failures=a[(a.file==BENCH)&a.residual_rows.gt(0)].set_index("rule_id")
        self.assertEqual(failures.loc["COORD_cust_lat","residual_rows"],1)
        self.assertEqual(failures.loc["DONG_FOREIGN_KEY","residual_rows"],1)
        self.assertEqual(failures.loc["16_TIME_MISMATCH","residual_rows"],26)
        self.assertEqual(failures.loc["BENCH_MISMATCH_FLAG","residual_rows"],26)

    def test_rowcount_log_balance(self):
        counts=pd.read_csv(ROOT/"outputs/clean_rowcount.csv",encoding="utf-8-sig")
        log=pd.read_csv(ROOT/"outputs/data_issues_log.csv",encoding="utf-8-sig")
        for row in counts.itertuples():
            entries=log[log.file.eq(row.file)]
            deleted=entries.loc[entries.action.str.startswith("REMOVE |"),"n_rows"].sum()
            split=entries.loc[entries.action.str.startswith("SPLIT |"),"n_rows"].sum()
            self.assertEqual(row.before_rows-row.after_rows,deleted+split)
            self.assertEqual(row.balance,0)


if __name__=="__main__":
    unittest.main()
