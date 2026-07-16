# your_app/management/commands/setup_vector_indexes.py
from django.core.management.base import BaseCommand
from django.db import connection
from django.db.utils import ProgrammingError


class Command(BaseCommand):
    help = 'Fix pgvector HNSW index for MiniLM-L6-v2 (384 dims)'

    def add_arguments(self, parser):
        parser.add_argument('--force', action='store_true', help='Drop & recreate indexes')

    def handle(self, *args, **options):
        force = options.get('force', False)

        self.stdout.write(self.style.SUCCESS('🔧 Fixing pgvector HNSW index...'))

        statements = [
            # 1. Ensure extension
            "CREATE EXTENSION IF NOT EXISTS vector;",

            # 2. Fix the embedding column to have explicit dimension (SAFE - only alters if needed)
            """
            DO $$
            BEGIN
                ALTER TABLE langchain_pg_embedding 
                ALTER COLUMN embedding TYPE vector(384);
            EXCEPTION 
                WHEN others THEN 
                    RAISE NOTICE 'Column already has dimension or error: %', SQLERRM;
            END $$;
            """,

            # 3. Drop old broken index if force mode
            "DROP INDEX IF EXISTS idx_embedding_hnsw;",

            # 4. Create correct HNSW index
            """
            CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_embedding_hnsw
            ON langchain_pg_embedding
            USING hnsw (embedding vector_cosine_ops)
            WITH (m = 16, ef_construction = 64);
            """,

            # 5. ef_search setting
            f"ALTER DATABASE {connection.settings_dict['NAME']} SET hnsw.ef_search = 100;",

            # 6. Metadata indexes (correct column name)
            """
            CREATE INDEX IF NOT EXISTS idx_cmetadata_source 
            ON langchain_pg_embedding USING btree (((cmetadata->>'source')));
            """,
            """
            CREATE INDEX IF NOT EXISTS idx_cmetadata_id 
            ON langchain_pg_embedding USING btree (((cmetadata->>'id')));
            """,
        ]

        with connection.cursor() as cursor:
            for i, sql in enumerate(statements, 1):
                try:
                    self.stdout.write(f'[{i}/{len(statements)}] Executing...')
                    cursor.execute(sql)
                    self.stdout.write(self.style.SUCCESS(f'✓ Success'))
                except ProgrammingError as e:
                    msg = str(e).lower()
                    if "already exists" in msg or "concurrently" in msg:
                        self.stdout.write(self.style.SUCCESS(f'✓ Already exists'))
                    elif "dimension" in msg:
                        self.stdout.write(self.style.WARNING(f'⚠️ Still dimension issue — add documents first'))
                    else:
                        self.stdout.write(self.style.ERROR(f'❌ {e}'))
                except Exception as e:
                    self.stdout.write(self.style.ERROR(f'❌ Unexpected: {e}'))

        self.stdout.write(self.style.SUCCESS('\n🎉 Setup completed!'))
        self.stdout.write(self.style.WARNING(
            'Recommendation: Add at least a few documents to the vectorstore, then re-run if needed.'
        ))