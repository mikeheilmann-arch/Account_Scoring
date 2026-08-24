import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from post_retrieval_quality_gate import decide


def item(name, website, company="Title company", action="score_now", legal_route="not_legal_entity"):
    return {"account": {"Name": name, "Website": website, "Company_Type__c": company, "Type": "Prospect", "Active_Customer__c": "false", "Account_Status__c": "Prospect", "ParentId": ""}, "score": {"ReviewAction": action, "RetrievalStatus": "ok", "LegalEntityRoute": legal_route, "Evidence": "retrieved public evidence", "EstimatedMCV": "500"}, "overlay": {}}


class PostRetrievalGateTests(unittest.TestCase):
    def test_wrong_site_controls_are_suppressed(self):
        for name, site, expected in [
            ("SB. Titles", "yourstatebank.com", "website_mismatch_review"),
            ("Master Settlement Services", "nextierbank.com", "website_mismatch_review"),
            ("Ionia County Title", "ioniacounty.org", "website_mismatch_review"),
            ("Title Companies In", "wlta.org", "website_mismatch_review"),
        ]:
            self.assertEqual(decide(item(name, site))[0], expected)

    def test_underwriter_and_abstract_controls(self):
        self.assertEqual(decide(item("Greco Title Agency", "atatitle.com"))[0], "underwriter_or_direct_side_review")
        self.assertEqual(decide(item("Northern New York Title Agency", "northernny.ctic.com"))[0], "underwriter_or_direct_side_review")
        self.assertEqual(decide(item("Denver Land Title", "ltgc.com"))[0], "parent_child_rollup_review")
        self.assertEqual(decide(item("Texas Capital Title", "ctot.com"))[0], "parent_child_rollup_review")
        self.assertEqual(decide(item("Real Estate Title Service", "retitleservice.com"))[0], "abstract_only_review")

    def test_valid_high_value_title_remains_scoreable(self):
        self.assertEqual(decide(item("Tennessee Title", "tennesseetitle.com"))[:2], ("scoreable_icp", "score_now"))

    def test_law_firm_must_use_legal_lane(self):
        disp, action, *_ = decide(item("Nixon Peabody", "nixonpeabody.com", company="Law firm", legal_route="not_legal_entity"))
        self.assertEqual((disp, action), ("legal_market_or_dominance_review", "route_ops_review"))

    def test_valid_law_firm_lane_can_retain_score(self):
        disp, action, *_ = decide(item("Valid Closing Counsel", "closingcounsel.com", company="Law firm", legal_route="legal_real_estate_closing_focused"))
        self.assertEqual((disp, action), ("scoreable_icp", "score_now"))

    def test_biglaw_general_practice_does_not_score(self):
        disp, action, *_ = decide(item("Proskauer", "proskauer.com", company="Law firm", legal_route="legal_general_practice_low_fit"))
        self.assertEqual((disp, action), ("legal_market_or_dominance_review", "route_ops_review"))

    def test_alta_is_not_used_by_decide_as_website_proof(self):
        row = item("SB. Titles", "yourstatebank.com")
        row["overlay"] = {"AltaMember": "true", "QualityEvidence": "ALTA match"}
        self.assertEqual(decide(row)[0], "website_mismatch_review")


if __name__ == "__main__":
    unittest.main()
