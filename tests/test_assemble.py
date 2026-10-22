# -*- coding: utf-8 -*-
import unittest

from exambank.assemble import Manifest, link_and_check, stitch_pages


def Q(number, stem, opts=(), cfp=False, cnp=False):
    return {"number": number, "stem": stem, "continues_from_previous_page": cfp, "continues_on_next_page": cnp,
            "options": [{"label": l, "text": t, "is_continuation": c} for (l, t, c) in opts]}


def A(number, correct=(), status=None, expl=None, cfp=False, cnp=False):
    return {"number": number, "correct": list(correct), "status": status, "explanation": expl,
            "continues_from_previous_page": cfp, "continues_on_next_page": cnp}


def page(n, role="questions", questions=(), answers=(), pdf="a"):
    return {"pdf": pdf, "page": n, "page_role": role, "questions": list(questions), "answers": list(answers)}


def opts4(prefix="گزینه"):
    return [(str(i), f"{prefix} {i}", False) for i in range(1, 5)]


class Stitching(unittest.TestCase):
    def test_cut_in_stem(self):
        p1 = page(1, questions=[Q(5, "یاخته‌ای که", cnp=True)])
        p2 = page(2, questions=[Q(None, "هسته ندارد کدام است؟", opts4(), cfp=True)])
        qs, _, issues = stitch_pages([p1, p2])
        self.assertEqual(len(qs), 1)
        self.assertEqual(qs[0]["number"], 5)
        self.assertEqual(qs[0]["stem_original"], "یاخته‌ای که هسته ندارد کدام است؟")
        self.assertEqual(qs[0]["pages"], [1, 2])
        self.assertEqual(len(qs[0]["options"]), 4)
        self.assertEqual(issues, [])

    def test_cut_between_options(self):
        p1 = page(1, questions=[Q(5, "صورت", [("1", "الف", False), ("2", "ب", False)], cnp=True)])
        p2 = page(2, questions=[Q(None, "", [("3", "ج", False), ("4", "د", False)], cfp=True)])
        qs, _, _ = stitch_pages([p1, p2])
        self.assertEqual([o["label"] for o in qs[0]["options"]], ["1", "2", "3", "4"])

    def test_cut_inside_option(self):
        p1 = page(1, questions=[Q(5, "صورت", [("1", "الف", False), ("2", "ب بریده", False)], cnp=True)])
        p2 = page(2, questions=[Q(None, "", [("2", "و ادامه", True), ("3", "ج", False), ("4", "د", False)], cfp=True)])
        qs, _, _ = stitch_pages([p1, p2])
        self.assertEqual(qs[0]["options"][1]["text"], "ب بریده و ادامه")
        self.assertEqual(len(qs[0]["options"]), 4)

    def test_orphan_text_goes_to_last_option(self):
        p1 = page(1, questions=[Q(5, "صورت", [("1", "الف", False), ("2", "ب", False)], cnp=True)])
        p2 = page(2, questions=[Q(None, "ادامه‌ی گزینه", [("3", "ج", False), ("4", "د", False)], cfp=True)])
        qs, _, _ = stitch_pages([p1, p2])
        self.assertEqual(qs[0]["options"][1]["text"], "ب ادامه‌ی گزینه")

    def test_three_pages_chain(self):
        p1 = page(1, questions=[Q(7, "الف", cnp=True)])
        p2 = page(2, questions=[Q(None, "ب", cfp=True, cnp=True)])
        p3 = page(3, questions=[Q(None, "ج", opts4(), cfp=True)])
        qs, _, _ = stitch_pages([p1, p2, p3])
        self.assertEqual((qs[0]["stem_original"], qs[0]["pages"]), ("الف ب ج", [1, 2, 3]))

    def test_other_questions_unaffected_and_order(self):
        p1 = page(1, questions=[Q(1, "اول", opts4()), Q(2, "دوم", cnp=True)])
        p2 = page(2, questions=[Q(None, "ادامه", opts4(), cfp=True), Q(3, "سوم", opts4())])
        qs, _, _ = stitch_pages([p2, p1])  # ورودی نامرتب
        self.assertEqual([q["number"] for q in qs], [1, 2, 3])

    def test_dangling_continuation(self):
        qs, _, issues = stitch_pages([page(1, questions=[Q(5, "x", cnp=True)])])
        self.assertEqual(len(qs), 1)
        self.assertEqual(issues[0]["code"], "dangling_continuation")

    def test_orphan_continuation(self):
        _, _, issues = stitch_pages([page(2, questions=[Q(None, "x", opts4(), cfp=True)])])
        self.assertIn("orphan_continuation", [i["code"] for i in issues])

    def test_no_stitch_across_page_gap_or_pdf(self):
        p1 = page(1, questions=[Q(5, "x", cnp=True)])
        p3 = page(3, questions=[Q(None, "y", opts4(), cfp=True)])
        qs, _, issues = stitch_pages([p1, p3])
        self.assertEqual(len(qs), 2)
        self.assertIn("dangling_continuation", [i["code"] for i in issues])
        qs, _, _ = stitch_pages([page(1, questions=[Q(5, "x", cnp=True)]),
                                 page(2, questions=[Q(None, "y", cfp=True)], pdf="b")])
        self.assertEqual(len(qs), 2)

    def test_number_mismatch_not_merged(self):
        p1 = page(1, questions=[Q(5, "x", cnp=True)])
        p2 = page(2, questions=[Q(6, "y", opts4(), cfp=True)])
        qs, _, issues = stitch_pages([p1, p2])
        self.assertEqual(len(qs), 2)
        self.assertIn("stitch_number_mismatch", [i["code"] for i in issues])

    def test_answer_explanation_stitch(self):
        p1 = page(1, "explanations", answers=[A(5, ["2"], expl="شروع", cnp=True)])
        p2 = page(2, "explanations", answers=[A(None, expl="ادامه", cfp=True)])
        _, ans, _ = stitch_pages([p1, p2])
        self.assertEqual(len(ans), 1)
        self.assertEqual((ans[0]["number"], ans[0]["explanation_original"]), (5, "شروع ادامه"))

    def test_input_not_mutated(self):
        p1 = page(1, questions=[Q(5, "x", cnp=True)])
        p2 = page(2, questions=[Q(None, "y", opts4(), cfp=True)])
        stitch_pages([p1, p2])
        self.assertEqual(p1["questions"][0]["stem"], "x")


class Linking(unittest.TestCase):
    def man(self, **kw):
        return Manifest.single("bio-1403-t", 1403, "تجربی", default_pdf="a", **kw)

    def run_link(self, pages, layout="auto", manifest=None):
        qs, ans, iss = stitch_pages(pages)
        return link_and_check(qs, ans, manifest or self.man(), layout, stitch_issues=iss)

    def codes(self, issues):
        return [i["code"] for i in issues]

    def test_interleaved(self):
        p = page(1, "mixed", questions=[Q(1, "a", opts4()), Q(2, "b", opts4())],
                 answers=[A(1, ["3"], expl="شرح ۱"), A(2, ["1"])])
        recs, issues = self.run_link([p], "interleaved")
        self.assertEqual([r["correct"] for r in recs], [["3"], ["1"]])
        self.assertEqual(recs[0]["explanation_original"], "شرح ۱")
        self.assertEqual(recs[0]["answer_status"], "official")
        self.assertEqual(issues, [])

    def test_separate_section_key_table(self):
        pq = page(1, questions=[Q(i, f"q{i}", opts4()) for i in (1, 2, 3)])
        pk = page(9, "answer_key", answers=[A(1, ["2"]), A(2, ["4"]), A(3, ["1"])])
        recs, issues = self.run_link([pq, pk], "separate")
        self.assertEqual([r["correct"] for r in recs], [["2"], ["4"], ["1"]])
        self.assertEqual(recs[0]["pages"], {"question": [1], "answer": [9]})
        self.assertEqual(recs[0]["answer_source"], "answer_key")

    def test_key_table_plus_explanations(self):
        pq = page(1, questions=[Q(1, "q", opts4())])
        pk = page(8, "answer_key", answers=[A(1, ["2"])])
        pe = page(9, "explanations", answers=[A(1, ["2"], expl="چون ...")])
        recs, issues = self.run_link([pq, pk, pe])
        self.assertEqual((recs[0]["correct"], recs[0]["explanation_original"], recs[0]["answer_source"]),
                         (["2"], "چون ...", "both"))
        self.assertEqual(issues, [])

    def test_conflicting_sources_flagged(self):
        pq = page(1, questions=[Q(1, "q", opts4())])
        pk = page(8, "answer_key", answers=[A(1, ["2"])])
        pe = page(9, "explanations", answers=[A(1, ["3"], expl="x")])
        recs, issues = self.run_link([pq, pk, pe])
        self.assertEqual(recs[0]["correct"], ["2"])
        self.assertIn("key_conflict_between_sources", self.codes(issues))

    def test_two_pdfs(self):
        man = Manifest([{"exam_id": "e1", "year_jalali": 1403, "group": "g", "question_pages": "1-5",
                         "answer_pages": "1-3", "question_pdf": "q", "answer_pdf": "k"}])
        pq = page(1, questions=[Q(1, "q", opts4())], pdf="q")
        pk = page(1, "answer_key", answers=[A(1, ["4"])], pdf="k")
        recs, issues = self.run_link([pq, pk], manifest=man)
        self.assertEqual(recs[0]["correct"], ["4"])
        self.assertEqual(issues, [])

    def test_multi_exam_manifest(self):
        man = Manifest([
            {"exam_id": "e1", "year_jalali": 1402, "group": "g", "question_pages": "1-2"},
            {"exam_id": "e2", "year_jalali": 1403, "group": "g", "question_pages": "3-4"}], default_pdf="a")
        pages = [page(1, "mixed", questions=[Q(1, "q", opts4())], answers=[A(1, ["1"])]),
                 page(3, "mixed", questions=[Q(1, "q", opts4())], answers=[A(1, ["2"])])]
        recs, _ = self.run_link(pages, manifest=man)
        self.assertEqual([(r["exam"]["exam_id"], r["correct"]) for r in recs], [("e1", ["1"]), ("e2", ["2"])])
        self.assertEqual(recs[1]["exam"]["year_jalali"], 1403)

    def test_issues(self):
        p = page(1, "mixed", questions=[
            Q(1, "a", opts4()), Q(3, "c", opts4()), Q(3, "dup", opts4()),
            Q(4, "d", [("1", "x", False), ("2", "y", False)])],
            answers=[A(1, ["7"]), A(9, ["1"])])
        recs, issues = self.run_link([p])
        c = self.codes(issues)
        for code in ("missing_question_numbers", "duplicate_question_number", "unusual_option_count",
                     "key_not_in_options", "question_without_answer", "answer_without_question"):
            self.assertIn(code, c)

    def test_annulled_and_multiple(self):
        p = page(1, "mixed", questions=[Q(1, "a", opts4()), Q(2, "b", opts4()), Q(3, "c", opts4())],
                 answers=[A(1, [], status="annulled"), A(2, ["1", "3"]), A(3, ["2"], status="disputed")])
        recs, issues = self.run_link([p])
        self.assertEqual([r["answer_status"] for r in recs], ["annulled", "multiple_keys", "disputed"])
        self.assertNotIn("question_without_answer", self.codes(issues))

    def test_layout_warnings(self):
        man = Manifest([{"exam_id": "e", "question_pages": "1-5", "answer_pages": "9-10"}], default_pdf="a")
        pq = page(1, questions=[Q(1, "q", opts4())])
        pk = page(9, "answer_key", answers=[A(1, ["2"])])
        # پاسخ در بخش جدا ولی layout=interleaved ← هشدار
        _, issues = self.run_link([pq, pk], "interleaved", man)
        self.assertIn("unexpected_answer_location", self.codes(issues))
        # layout درست ← بدون هشدار
        _, issues = self.run_link([pq, pk], "separate", man)
        self.assertNotIn("unexpected_answer_location", self.codes(issues))
        # پاسخ کنار سوال ولی layout=separate ← هشدار
        pi = page(2, "mixed", questions=[Q(2, "q", opts4())], answers=[A(2, ["1"])])
        _, issues = self.run_link([pq, pi, pk], "separate", man)
        self.assertIn("unexpected_answer_location", self.codes(issues))

    def test_page_outside_manifest(self):
        man = Manifest([{"exam_id": "e", "question_pages": "1-5"}], default_pdf="a")
        _, issues = self.run_link([page(20, questions=[Q(1, "q", opts4())])], manifest=man)
        self.assertIn("question_outside_manifest", self.codes(issues))

    def test_labels_normalized(self):
        p = page(1, "mixed", questions=[Q(1, "a", [("۱)", "x", False), ("۲)", "y", False), ("۳)", "z", False), ("۴)", "w", False)])],
                 answers=[A(1, ["۳"])])
        recs, issues = self.run_link([p])
        self.assertEqual([o["label"] for o in recs[0]["options"]], ["1", "2", "3", "4"])
        self.assertEqual(recs[0]["correct"], ["3"])
        self.assertEqual(issues, [])


if __name__ == "__main__":
    unittest.main()
