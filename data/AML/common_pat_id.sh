#!/usr/bin/env bash
#
# Create a list of patients ID common to all the datasets

cut -d',' -f1 AML_gene_espression2.csv | uniq | sort | uniq > AML_gene_expression_pat_id.txt
cut -d',' -f1 AML_miRNA_Seq2.csv | uniq | sort | uniq > AML_miRNA_Seq_pat_id.txt
cut -d',' -f1 AML_somatic_mutations2.txt | uniq | sort | uniq > AML_somatic_mutations_pat_id.txt

# we loop on AML_somatic_mutations as it has the largest number of patient id
for p in `cat AML_somatic_mutations_pat_id.txt`; do
  echo $p;
  if [ `grep -c $p AML_gene_expression_pat_id.txt` == 1 ]; then
    if [ `grep -c $p AML_miRNA_Seq_pat_id.txt` == 1 ]; then
      echo $p >> pat_id.txt
    fi
  fi
done
