import unittest

from services.matcher import analyse_match, extract_requirements, format_analysis, tokens


def keys(result, section="strong_matches"):
    return {r["key"] for r in result[section]}


class MatcherTests(unittest.TestCase):
    def test_all_seven_categories(self):
        text = ("Python required. Power BI required. 3 years of experience. Bachelor's degree. "
                "English required. Based in Dubai. Own visa required. Communication skills.")
        result = analyse_match(text, text)
        self.assertEqual({r["category"] for r in result["requirements"]},
                         {"hard_skills", "tools", "experience", "education", "languages", "location", "soft_skills"})
        self.assertEqual(result["score"], 100)

    def test_stopwords_alone_are_not_requirements(self):
        noise = "good looking preferred advantage basic skills knowledge"
        self.assertEqual(tokens(noise), [])
        self.assertEqual(extract_requirements(noise), [])
        self.assertIsNone(analyse_match("", noise)["score"])

    def test_adding_filler_does_not_change_score(self):
        first = analyse_match("SQL", "SQL required. Python required.")
        repeated = analyse_match("SQL", "SQL required. Python required. " + "good looking skills knowledge. " * 50)
        self.assertEqual(first["score"], repeated["score"])

    def test_frequency_does_not_increase_importance(self):
        first = analyse_match("SQL", "SQL. Python.")
        repeated = analyse_match("SQL", "SQL. " * 50 + "Python.")
        self.assertEqual(first["score"], repeated["score"])
        self.assertEqual(len(repeated["requirements"]), 2)

    def test_synonyms_and_case(self):
        for cv, vacancy in [("PowerBI", "Power BI"), ("MS Excel", "Microsoft Excel"),
                            ("Excel", "MS Excel"), ("T-SQL", "Structured Query Language"),
                            ("PL/SQL", "SQL"), ("Python3.11", "Python programming language"),
                            ("Based in UAE", "Based in United Arab Emirates"),
                            ("Based in Dubai", "Dubai")]:
            with self.subTest(cv=cv):
                self.assertEqual(analyse_match(cv, vacancy)["score"], 100)

    def test_sql_dialects_are_directional(self):
        self.assertIn("sql", keys(analyse_match("PostgreSQL", "SQL")))
        self.assertIn("postgresql", keys(analyse_match("SQL", "PostgreSQL"), "important_gaps"))

    def test_boundaries(self):
        self.assertEqual(keys(analyse_match("NoSQL JavaScript excellent", "SQL. Java. Excel.")), set())

    def test_weighted_score_not_token_percentage(self):
        vacancy = "Python. Excel. 3 years experience. Bachelor's degree. English. Communication."
        self.assertEqual(analyse_match("Python", vacancy)["score"], 35)
        self.assertEqual(analyse_match("Communication", vacancy)["score"], 5)
        self.assertEqual(analyse_match("Excel", "Python. Excel.")["score"], 36)

    def test_languages_and_location_share_ten_percent(self):
        vacancy = "Python. Excel. 3 years experience. Bachelor's degree. English. Dubai. Communication."
        self.assertEqual(analyse_match("English", vacancy)["score"], 5)
        self.assertEqual(analyse_match("English. Based in Dubai.", vacancy)["score"], 10)

    def test_optional_requirements_and_headings(self):
        result = analyse_match("SQL", "Required skills:\nSQL\nPreferred skills:\nPython\nTableau\nRequirements:\nExcel")
        self.assertEqual(keys(result, "optional_gaps"), {"python", "tableau"})
        self.assertEqual(keys(result, "important_gaps"), {"excel"})
        result = analyse_match("SQL", "SQL required and Python preferred. Excel is an advantage.")
        self.assertEqual(keys(result, "optional_gaps"), {"python", "excel"})

    def test_optional_weight_and_mandatory_override(self):
        self.assertEqual(analyse_match("SQL", "SQL required. Python preferred.")["score"], 80)
        self.assertLess(analyse_match("SQL", "SQL required. Python required.")["score"], 80)
        result = analyse_match("", "Python preferred. Python required.")
        self.assertEqual(keys(result, "important_gaps"), {"python"})
        self.assertFalse(result["optional_gaps"])

    def test_inline_optional_heading(self):
        result = analyse_match("", "Nice-to-have: Python, Excel\nRequired: SQL")
        self.assertEqual(keys(result, "optional_gaps"), {"python", "excel"})
        self.assertEqual(keys(result, "important_gaps"), {"sql"})
        result = analyse_match("", "SQL required. Optional: Python.")
        self.assertEqual(keys(result, "important_gaps"), {"sql"})
        self.assertEqual(keys(result, "optional_gaps"), {"python"})

    def test_or_is_one_requirement(self):
        result = analyse_match("SQL", "Python or SQL required.")
        self.assertEqual(result["score"], 100)
        self.assertEqual(len(result["requirements"]), 1)
        self.assertFalse(result["important_gaps"])

    def test_negated_and_learning_claims_are_not_evidence(self):
        for cv in ("No Python experience", "Learning Python", "Python not used", "Without Python"):
            self.assertFalse(keys(analyse_match(cv, "Python required")))
        self.assertFalse(extract_requirements("Python not required. No experience required."))
        self.assertFalse(keys(analyse_match("PMP in progress", "PMP required")))

    def test_numeric_experience_and_scope(self):
        result = analyse_match("2 years of experience in Python. 10 years experience in accounting.",
                               "Minimum 3 years experience in Python.")
        self.assertIn("years:python", keys(result, "important_gaps"))
        self.assertIn("years:python", keys(analyse_match("5 years experience in Python", "3-5 years experience in Python")))
        self.assertIn("years:", keys(analyse_match("Five years of experience", "3+ years of experience")))

    def test_unrelated_specialization_does_not_satisfy_years(self):
        result = analyse_match("5 years experience in hospitality", "3 years experience in engineering")
        self.assertFalse(result["strong_matches"])
        result = analyse_match("5 years marketing experience", "3 years engineering experience")
        self.assertFalse(result["strong_matches"])

    def test_total_and_specialist_years_do_not_get_combined(self):
        result = analyse_match("8 years total experience and 1 year Python experience", "3 years Python experience")
        self.assertIn("years:python", keys(result, "important_gaps"))

    def test_optional_higher_experience_is_not_mandatory(self):
        result = analyse_match("3 years Python experience", "3 years Python experience required. 5 years Python experience preferred.")
        self.assertFalse(result["important_gaps"])
        self.assertTrue(any("5+ years" in r["label"] for r in result["optional_gaps"]))

    def test_no_invented_years_from_dates_or_skill_lists(self):
        result = analyse_match("Python. Analyst 2020-2026.", "3 years Python experience")
        self.assertIn("years:python", keys(result, "important_gaps"))
        self.assertFalse(extract_requirements("Company founded 20 years ago"))

    def test_language_proficiency_is_not_assumed(self):
        result = analyse_match("Basic English", "Fluent English required")
        self.assertIn("english", keys(result, "important_gaps"))
        self.assertIn("proficiency", result["important_gaps"][0]["reason"])
        self.assertEqual(analyse_match("English fluent", "Fluent English")["score"], 100)

    def test_uae_experience_is_not_current_location(self):
        result = analyse_match("3 years Dubai experience", "UAE experience required. Based in Dubai required.")
        self.assertIn("uae_experience", keys(result))
        self.assertIn("dubai", keys(result, "important_gaps"))
        self.assertFalse(keys(analyse_match("Worked in Dubai in 2020", "Dubai")))

    def test_relocation_and_visa_benefits_do_not_imply_eligibility(self):
        self.assertFalse(keys(analyse_match("Willing to relocate to Dubai", "Based in Dubai")))
        self.assertFalse(extract_requirements("UAE visa sponsorship provided"))
        result = analyse_match("Based in Dubai", "Own visa required. UAE work authorization required.")
        self.assertEqual(keys(result, "important_gaps"), {"own_visa", "work_authorization"})

    def test_education_completion(self):
        self.assertFalse(keys(analyse_match("Pursuing a bachelor's degree", "Bachelor's degree")))
        self.assertEqual(analyse_match("BSc", "University degree required")["score"], 100)
        self.assertFalse(keys(analyse_match("Bachelor's degree", "PMP certification")))

    def test_degree_subject_is_not_invented(self):
        result = analyse_match("Bachelor's degree in History", "Bachelor's degree in Computer Science required")
        self.assertIn("bachelor", keys(result, "important_gaps"))
        result = analyse_match("BSc Computer Science", "Bachelor's degree in Computer Science")
        self.assertIn("bachelor", keys(result))

    def test_output_and_truthful_recommendations(self):
        report = format_analysis(analyse_match("SQL", "SQL. Python required. Excel preferred."))
        for heading in ("Overall match score", "Strong matches", "Important gaps", "Optional gaps", "Recommendations"):
            self.assertIn(heading, report)
        self.assertIn("not an official ATS score", report)
        self.assertIn("Never add skills, experience or credentials you do not have", report)
        self.assertNotIn("adding relevant missing keywords", report)
        self.assertIn("N/A", format_analysis(analyse_match("", "Good candidate wanted")))


if __name__ == "__main__":
    unittest.main()
