"""Guided question tree: positional mirroring, translation (mocked), cached
reads, canonical-only writes, and the HTTP surface."""

from __future__ import annotations

from unittest import mock

from django.core.cache import cache
from django.test import override_settings
from django.urls import reverse
from rest_framework.test import APITestCase

from knowledge.models import AvailableLanguage, QuestionTreeNode
from knowledge.services import guided_tree as gt

from .factories import make_tenant


def fake_translate(text, target_lang, *, source_lang=None):
    """Deterministic stand-in for the LLM: prefix the target lang code."""
    if not text or not text.strip():
        return text or ""
    return f"[{target_lang}] {text}"


# Run translation inline so tests are deterministic; canonical = 'ar'.
GT_SETTINGS = dict(
    GUIDED_TREE_TRANSLATE_MODE="sync",
    GUIDED_TREE_CANONICAL_LANGUAGE="ar",
    GUIDED_TREE_CACHE_TTL=300,
)


@override_settings(**GT_SETTINGS)
class MirroringTests(APITestCase):
    def setUp(self):
        self.user, _ = make_tenant("tree_owner")
        AvailableLanguage.objects.create(code="ar", name="Arabic")
        AvailableLanguage.objects.create(code="en", name="English")
        cache.clear()
        self.p = mock.patch.object(gt, "translate_text", side_effect=fake_translate)
        self.p.start()
        self.addCleanup(self.p.stop)

    def test_create_canonical_generates_mirror(self):
        root = gt.create_node(owner=self.user, title="ما هي الخدمة؟", answer="نحن نبيع")
        en = QuestionTreeNode.objects.get(owner=self.user, language="en")
        self.assertEqual(en.title, "[en] ما هي الخدمة؟")
        self.assertEqual(en.answer, "[en] نحن نبيع")
        self.assertEqual(en.order, root.order)
        self.assertIsNone(en.parent)

    def test_mirror_matches_by_position_not_fk(self):
        root = gt.create_node(owner=self.user, title="root")
        child = gt.create_node(owner=self.user, parent=root, title="child")
        en_child = gt.mirror_of(child, "en")
        self.assertIsNotNone(en_child)
        self.assertEqual(en_child.language, "en")
        self.assertEqual(gt.position_path(en_child), gt.position_path(child))
        # canonical_of walks back from the mirror to the source.
        self.assertEqual(gt.canonical_of(en_child).id, child.id)

    def test_order_autoincrement_and_uniqueness(self):
        a = gt.create_node(owner=self.user, title="a")
        b = gt.create_node(owner=self.user, title="b")
        self.assertEqual([a.order, b.order], [0, 1])
        with self.assertRaises(ValueError):
            gt.create_node(owner=self.user, title="dup", order=0)  # sibling order taken

    def test_edit_targets_canonical_and_retranslates(self):
        root = gt.create_node(owner=self.user, title="old")
        en = gt.mirror_of(root, "en")
        # Edit via the MIRROR node — must resolve to canonical and update both.
        gt.update_node(en, title="new")
        root.refresh_from_db()
        self.assertEqual(root.title, "new")
        self.assertEqual(gt.mirror_of(root, "en").title, "[en] new")

    def test_delete_removes_all_mirrors_and_canonical(self):
        root = gt.create_node(owner=self.user, title="x")
        gt.create_node(owner=self.user, parent=root, title="y")
        self.assertEqual(QuestionTreeNode.objects.filter(owner=self.user).count(), 4)  # 2 ar + 2 en
        gt.delete_node(gt.mirror_of(root, "en"))  # delete via a mirror
        self.assertEqual(QuestionTreeNode.objects.filter(owner=self.user).count(), 0)

    def test_resync_language_rebuilds_from_canonical(self):
        gt.create_node(owner=self.user, title="one")
        gt.create_node(owner=self.user, title="two")
        QuestionTreeNode.objects.filter(owner=self.user, language="en").delete()
        self.assertEqual(QuestionTreeNode.objects.filter(owner=self.user, language="en").count(), 0)
        gt.resync_language(self.user, "en")
        self.assertEqual(QuestionTreeNode.objects.filter(owner=self.user, language="en").count(), 2)


@override_settings(**GT_SETTINGS)
class TranslateTextTests(APITestCase):
    def test_empty_is_passthrough(self):
        self.assertEqual(gt.translate_text("", "en"), "")
        self.assertEqual(gt.translate_text(None, "en"), "")

    def test_strips_quotes(self):
        class LLM:
            def complete(self, s, u, *, temperature=0):
                return '"Hello"'
        with mock.patch("knowledge.services.llm.get_backend", return_value=LLM()):
            self.assertEqual(gt.translate_text("مرحبا", "en"), "Hello")

    def test_returns_original_on_total_failure(self):
        with mock.patch("knowledge.services.llm.get_backend", side_effect=RuntimeError):
            self.assertEqual(gt.translate_text("مرحبا", "en"), "مرحبا")  # never throws


@override_settings(**GT_SETTINGS)
class CachedReadTests(APITestCase):
    def setUp(self):
        self.user, _ = make_tenant("reader")
        AvailableLanguage.objects.create(code="ar", name="Arabic")
        cache.clear()

    def test_build_tree_is_nested_and_ordered(self):
        with mock.patch.object(gt, "active_target_languages", return_value=[]):
            root = gt.create_node(owner=self.user, title="root")
            gt.create_node(owner=self.user, parent=root, title="c0", order=0)
            gt.create_node(owner=self.user, parent=root, title="c1", order=1)
        tree = gt.build_tree(self.user, "ar")
        self.assertEqual(len(tree), 1)
        self.assertEqual([c["title"] for c in tree[0]["children"]], ["c0", "c1"])

    def test_cache_is_used_and_invalidated_on_write(self):
        with mock.patch.object(gt, "active_target_languages", return_value=[]):
            gt.create_node(owner=self.user, title="first")
        gt.get_tree(self.user, "ar")  # populate cache
        self.assertIsNotNone(cache.get(f"guided_tree:{self.user.pk}:ar"))
        with mock.patch.object(gt, "active_target_languages", return_value=[]):
            gt.create_node(owner=self.user, title="second")  # write invalidates
        self.assertIsNone(cache.get(f"guided_tree:{self.user.pk}:ar"))


@override_settings(**GT_SETTINGS)
class GuidedTreeApiTests(APITestCase):
    def setUp(self):
        self.user, self.key = make_tenant("api_owner")
        self.admin, self.admin_key = make_tenant("api_admin", is_staff=True)
        AvailableLanguage.objects.create(code="ar", name="Arabic")
        AvailableLanguage.objects.create(code="en", name="English")
        cache.clear()
        self.p = mock.patch.object(gt, "translate_text", side_effect=fake_translate)
        self.p.start()
        self.addCleanup(self.p.stop)

    def test_requires_auth(self):
        self.assertEqual(self.client.get(reverse("guided-tree-list")).status_code, 401)

    def test_create_and_read_tree(self):
        res = self.client.post(
            reverse("guided-tree-list"),
            {"title": "سؤال", "answer": "جواب"}, format="json", HTTP_X_API_KEY=self.key,
        )
        self.assertEqual(res.status_code, 201)
        # Canonical tree
        ar = self.client.get(reverse("guided-tree-list"), HTTP_X_API_KEY=self.key)
        self.assertEqual(ar.data["language"], "ar")
        self.assertEqual(len(ar.data["tree"]), 1)
        # Mirror tree
        en = self.client.get(reverse("guided-tree-list") + "?language=en", HTTP_X_API_KEY=self.key)
        self.assertEqual(en.data["tree"][0]["title"], "[en] سؤال")

    def test_duplicate_order_rejected(self):
        self.client.post(reverse("guided-tree-list"), {"title": "a", "order": 0}, format="json", HTTP_X_API_KEY=self.key)
        res = self.client.post(reverse("guided-tree-list"), {"title": "b", "order": 0}, format="json", HTTP_X_API_KEY=self.key)
        self.assertEqual(res.status_code, 400)

    def test_tenant_isolation_on_read(self):
        gt.create_node(owner=self.admin, title="admin-only")
        res = self.client.get(reverse("guided-tree-list"), HTTP_X_API_KEY=self.key)
        self.assertEqual(res.data["tree"], [])  # owner sees none of admin's nodes

    def test_delete_via_api(self):
        node = gt.create_node(owner=self.user, title="del")
        res = self.client.delete(reverse("guided-tree-detail", args=[node.id]), HTTP_X_API_KEY=self.key)
        self.assertEqual(res.status_code, 204)
        self.assertEqual(QuestionTreeNode.objects.filter(owner=self.user).count(), 0)

    def test_languages_list_and_add_remove(self):
        # list
        res = self.client.get(reverse("tree-language-list"), HTTP_X_API_KEY=self.key)
        self.assertEqual({l["code"] for l in res.data}, {"ar", "en"})
        # non-admin cannot add
        res = self.client.post(reverse("tree-language-list"), {"code": "fr", "name": "French"},
                               format="json", HTTP_X_API_KEY=self.key)
        self.assertEqual(res.status_code, 403)
        # admin adds
        res = self.client.post(reverse("tree-language-list"), {"code": "fr", "name": "French"},
                               format="json", HTTP_X_API_KEY=self.admin_key)
        self.assertEqual(res.status_code, 201)
        # canonical cannot be removed
        res = self.client.delete(reverse("tree-language-detail", args=["ar"]), HTTP_X_API_KEY=self.admin_key)
        self.assertEqual(res.status_code, 400)

    def test_cannot_add_canonical_language(self):
        res = self.client.post(reverse("tree-language-list"), {"code": "ar", "name": "Arabic"},
                               format="json", HTTP_X_API_KEY=self.admin_key)
        self.assertEqual(res.status_code, 400)

    def test_retrieve_node_and_404(self):
        node = gt.create_node(owner=self.user, title="one")
        ok = self.client.get(reverse("guided-tree-detail", args=[node.id]), HTTP_X_API_KEY=self.key)
        self.assertEqual(ok.status_code, 200)
        self.assertEqual(ok.data["title"], "one")
        missing = self.client.get(
            reverse("guided-tree-detail", args=["00000000-0000-0000-0000-000000000000"]),
            HTTP_X_API_KEY=self.key,
        )
        self.assertEqual(missing.status_code, 404)

    def test_create_with_foreign_parent_rejected(self):
        other_root = gt.create_node(owner=self.admin, title="admins")
        res = self.client.post(
            reverse("guided-tree-list"),
            {"title": "child", "parent": str(other_root.id)}, format="json", HTTP_X_API_KEY=self.key,
        )
        self.assertEqual(res.status_code, 400)

    def test_patch_node_success_and_404(self):
        node = gt.create_node(owner=self.user, title="old")
        ok = self.client.patch(
            reverse("guided-tree-detail", args=[node.id]), {"title": "new"},
            format="json", HTTP_X_API_KEY=self.key,
        )
        self.assertEqual(ok.status_code, 200)
        self.assertEqual(ok.data["title"], "new")
        missing = self.client.patch(
            reverse("guided-tree-detail", args=["00000000-0000-0000-0000-000000000000"]),
            {"title": "x"}, format="json", HTTP_X_API_KEY=self.key,
        )
        self.assertEqual(missing.status_code, 404)

    def test_patch_duplicate_order_rejected(self):
        gt.create_node(owner=self.user, title="a", order=0)
        b = gt.create_node(owner=self.user, title="b", order=1)
        res = self.client.patch(
            reverse("guided-tree-detail", args=[b.id]), {"order": 0},
            format="json", HTTP_X_API_KEY=self.key,
        )
        self.assertEqual(res.status_code, 400)

    def test_delete_404(self):
        res = self.client.delete(
            reverse("guided-tree-detail", args=["00000000-0000-0000-0000-000000000000"]),
            HTTP_X_API_KEY=self.key,
        )
        self.assertEqual(res.status_code, 404)

    def test_flat_dump(self):
        root = gt.create_node(owner=self.user, title="r")
        gt.create_node(owner=self.user, parent=root, title="c")
        res = self.client.get(reverse("guided-tree-flat") + "?language=ar", HTTP_X_API_KEY=self.key)
        self.assertEqual(res.status_code, 200)
        self.assertEqual(len(res.data), 2)

    def test_retranslate_endpoint(self):
        gt.create_node(owner=self.user, title="r")
        res = self.client.post(
            reverse("guided-tree-retranslate"), {"language": "en"},
            format="json", HTTP_X_API_KEY=self.key,
        )
        self.assertEqual(res.status_code, 202)
        self.assertEqual(res.data["languages"], ["en"])

    def test_remove_language_success_and_404(self):
        AvailableLanguage.objects.create(code="fr", name="French")
        gt.create_node(owner=self.user, title="r")  # creates fr mirror too
        ok = self.client.delete(reverse("tree-language-detail", args=["fr"]), HTTP_X_API_KEY=self.admin_key)
        self.assertEqual(ok.status_code, 204)
        self.assertFalse(AvailableLanguage.objects.filter(code="fr").exists())
        self.assertEqual(QuestionTreeNode.objects.filter(owner=self.user, language="fr").count(), 0)
        missing = self.client.delete(reverse("tree-language-detail", args=["zz"]), HTTP_X_API_KEY=self.admin_key)
        self.assertEqual(missing.status_code, 404)

    def test_remove_language_non_admin_forbidden(self):
        res = self.client.delete(reverse("tree-language-detail", args=["en"]), HTTP_X_API_KEY=self.key)
        self.assertEqual(res.status_code, 403)


@override_settings(**GT_SETTINGS)
class ServiceBranchTests(APITestCase):
    def setUp(self):
        self.user, _ = make_tenant("svc_owner")
        AvailableLanguage.objects.create(code="ar", name="Arabic")
        AvailableLanguage.objects.create(code="en", name="English")
        cache.clear()
        self.p = mock.patch.object(gt, "translate_text", side_effect=fake_translate)
        self.p.start()
        self.addCleanup(self.p.stop)

    def test_canonical_of_and_mirror_of_identity(self):
        root = gt.create_node(owner=self.user, title="r")
        self.assertEqual(gt.canonical_of(root).id, root.id)     # already canonical
        self.assertEqual(gt.mirror_of(root, "ar").id, root.id)  # same language

    def test_order_change_triggers_resync(self):
        a = gt.create_node(owner=self.user, title="a", order=0)
        gt.create_node(owner=self.user, title="b", order=1)
        # move a to order 5 (free) → mirrors resync to the new address
        gt.update_node(a, order=5)
        en = gt.mirror_of(QuestionTreeNode.objects.get(pk=a.pk), "en")
        self.assertIsNotNone(en)
        self.assertEqual(en.order, 5)

    def test_resync_language_noop_for_canonical(self):
        gt.create_node(owner=self.user, title="r")
        before = QuestionTreeNode.objects.filter(owner=self.user, language="ar").count()
        gt.resync_language(self.user, "ar")  # canonical → no-op
        self.assertEqual(QuestionTreeNode.objects.filter(owner=self.user, language="ar").count(), before)

    @override_settings(GUIDED_TREE_CACHE_TTL=0)
    def test_get_tree_without_cache(self):
        gt.create_node(owner=self.user, title="r")
        tree = gt.get_tree(self.user, "ar")  # ttl=0 → build directly, no cache write
        self.assertEqual(len(tree), 1)
        self.assertIsNone(cache.get(f"guided_tree:{self.user.pk}:ar"))

    def test_translate_subtree_isolates_child_failure(self):
        # Build the canonical tree WITHOUT generating mirrors, then translate the
        # subtree with one child's translation blowing up — the sibling must still
        # get its mirror (per-child failure isolation).
        with mock.patch.object(gt, "active_target_languages", return_value=[]):
            root = gt.create_node(owner=self.user, title="root")
            gt.create_node(owner=self.user, parent=root, title="bad", order=0)
            gt.create_node(owner=self.user, parent=root, title="good", order=1)

        def flaky(text, target_lang, *, source_lang=None):
            if text == "bad":
                raise RuntimeError("translate boom")
            return f"[{target_lang}] {text}"

        with mock.patch.object(gt, "translate_text", side_effect=flaky):
            gt.translate_subtree(root, "en")
        titles = set(
            QuestionTreeNode.objects.filter(owner=self.user, language="en").values_list("title", flat=True)
        )
        self.assertIn("[en] good", titles)   # sibling survived the bad child

    def test_resync_isolates_root_failure(self):
        with mock.patch.object(gt, "active_target_languages", return_value=[]):
            gt.create_node(owner=self.user, title="bad", order=0)
            gt.create_node(owner=self.user, title="good", order=1)

        def flaky(text, target_lang, *, source_lang=None):
            if text == "bad":
                raise RuntimeError("boom")
            return f"[{target_lang}] {text}"

        with mock.patch.object(gt, "translate_text", side_effect=flaky):
            gt.resync_language(self.user, "en")  # one root fails, the other completes
        self.assertTrue(
            QuestionTreeNode.objects.filter(owner=self.user, language="en", title="[en] good").exists()
        )


class TranslateFallbackTests(APITestCase):
    def test_fallback_provider_used_when_primary_fails(self):
        class Primary:
            def complete(self, s, u, *, temperature=0):
                raise RuntimeError("primary down")
        class Fallback:
            def complete(self, s, u, *, temperature=0):
                return "translated"
        backends = iter([Primary(), Fallback()])
        with mock.patch("knowledge.services.llm.get_backend", side_effect=lambda m=None: next(backends)):
            self.assertEqual(gt.translate_text("مرحبا", "en"), "translated")
